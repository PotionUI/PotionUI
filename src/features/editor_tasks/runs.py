import asyncio
import io
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import numpy as np
from PIL import Image

from src.features.editor_tasks import regions
from src.features.editor_tasks.declarations import (
    derived_quantity_field,
    derived_seed_field,
    image_field_name,
    mode_fields,
)
from src.features.editor_tasks.dto import MAX_CANDIDATES, StartEditorRunRequest
from src.features.editor_tasks.manager import EditorTaskManager, ResolvedOffer
from src.features.editor_tasks.records import (
    CANCELLED,
    DONE,
    FAILED,
    ID_PREFIX,
    RUNNING,
    EditorRun,
)
from src.features.editor_tasks.regions import Geometry
from src.features.editor_tasks.repository import EditorRunRepository
from src.features.generation.dto import GenerationRequest
from src.features.generation.hooks import GENERATION_HOOKS
from src.features.media.validators import UPLOAD_PURPOSE_DERIVED, UPLOAD_PURPOSE_EDITOR_RUN
from src.platform.database.rows import now_iso
from src.platform.http.errors import DomainError, NotFoundError
from src.platform.media_tools.fastpath import prepare_image
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

SUBSCRIBER_ID = "editor_runs"
MESSAGE_TYPE = "editor_run_update"
RESTART_MESSAGE = "The app restarted while this was generating"
NO_PICTURE = "The model returned no picture"
NOT_PLACED = "The result could not be placed on the picture"
GENERATION_FAILED = "The generation failed"
GENERATION_GONE = "The generation could not be found"
NOT_FINISHED = "The run could not be finished"
TERMINAL_GENERATION = frozenset({"completed", "failed", "cancelled"})
PNG_LEVEL = 1
ORIGIN_KIND = "editor"

Submit = Callable[[GenerationRequest, Any], Awaitable[Dict[str, Any]]]
CancelGeneration = Callable[[str, Any], Awaitable[Any]]


def hook_data(context: Any) -> Dict[str, Any]:
    data = getattr(context, "data", None)
    if isinstance(data, dict):
        return data
    return context if isinstance(context, dict) else {}


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=PNG_LEVEL)
    return buffer.getvalue()


@dataclass
class PreparedRun:
    geometry: Geometry
    input_png: bytes
    mask_png: Optional[bytes]
    base_png: bytes
    region_png: Optional[bytes]


class EditorRunManager:

    def __init__(
        self,
        repository: EditorRunRepository,
        tasks: EditorTaskManager,
        edits: Any,
        media_tools: Any,
        media_store: Any,
        submit: Submit,
        cancel_generation: CancelGeneration,
        generations: Any,
        parameters: Any,
        hub: Any,
        clock: Callable[[], str] = now_iso,
        new_id: Callable[[], str] = generate_ulid,
    ):
        self.repository = repository
        self.tasks = tasks
        self.edits = edits
        self.media_tools = media_tools
        self.media_store = media_store
        self._submit = submit
        self._cancel_generation = cancel_generation
        self.generations = generations
        self.parameters = parameters
        self.hub = hub
        self.clock = clock
        self.new_id = new_id
        self._live: Dict[str, str] = {}
        self._finishing: Set[str] = set()
        self._holds: Dict[str, int] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def subscribe(self, hook_chain: Any) -> None:
        hook_chain.register(GENERATION_HOOKS.after_complete, SUBSCRIBER_ID, self.on_generation_complete)

    def unsubscribe(self, hook_chain: Any) -> None:
        hook_chain.unregister(GENERATION_HOOKS.after_complete, SUBSCRIBER_ID)

    def on_generation_complete(self, context: Any) -> Any:
        try:
            data = hook_data(context)
            run_id = self._live.get(data.get("generation_id"))
            if run_id is not None:
                self._schedule(run_id, data.get("status"))
        except Exception:
            logger.exception("Editor runs: the completion hook failed")
        return context

    def _schedule(self, run_id: str, hint: Optional[str] = None) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._finish(run_id, hint), loop)

    async def start(self, user: Any, task_id: str, body: StartEditorRunRequest) -> Dict[str, Any]:
        self._loop = asyncio.get_running_loop()
        offer = await asyncio.to_thread(
            self.tasks.resolve_offer, user, task_id, body.setup.preset_id, body.setup.mode
        )
        await asyncio.to_thread(self.edits.get, user.id, body.edit.id)
        prompt = self._prompt(offer, body.prompt)
        region_ref = body.region if offer.task.region != "none" else None
        if offer.task.region == "required" and region_ref is None:
            raise DomainError("region_required", "Mark the area first", 422)
        prepared = await asyncio.to_thread(self._prepare, user, offer, body, region_ref)
        stored: Dict[str, Any] = {}
        run_id = ID_PREFIX + self.new_id()
        released = False
        try:
            await self._store(user.id, prepared, stored)
            origin = {
                "kind": ORIGIN_KIND,
                "edit_id": body.edit.id,
                "step_id": body.edit.step_id,
                "task": task_id,
                "run_id": run_id,
            }
            request = GenerationRequest(
                preset_id=body.setup.preset_id,
                prompt=prompt,
                mode=body.setup.mode,
                form_data=self._form(offer, body, stored, prepared.geometry),
                form_name=body.setup.variant,
            )
            run = EditorRun(
                id=run_id,
                user_id=user.id,
                task=task_id,
                edit_id=body.edit.id,
                step_id=body.edit.step_id,
                rect=prepared.geometry.rect(),
                crop={
                    **prepared.geometry.to_dict(),
                    "files": {name: item.id for name, item in stored.items()},
                    "names": {name: item.filename for name, item in stored.items()},
                },
            )
            await asyncio.to_thread(self.repository.create, run, self.clock())
            self._release(stored)
            released = True
            try:
                submitted = await self._submit(request, user)
                generation_id = submitted["generation_id"]
                if not await self._tag(generation_id, origin):
                    await self._abort_generation(generation_id, user)
                    raise DomainError("run_start_failed", "The run could not be started, try again", 500)
            except BaseException:
                await asyncio.to_thread(self.repository.delete, run_id)
                raise
        except BaseException:
            if not released:
                self._release(stored)
            await self._discard(user.id, self._entries(stored), run_id)
            raise
        self._live[generation_id] = run_id
        await asyncio.to_thread(self.repository.update, run_id, self.clock(), generation_id=generation_id)
        current = await asyncio.to_thread(self.repository.find, run_id)
        await self._announce(current)
        generation = await asyncio.to_thread(self.generations.get_by_id, generation_id)
        status = getattr(generation, "status", None)
        if status in TERMINAL_GENERATION:
            self._schedule(run_id, status)
        return current.to_dict()

    async def get(self, user: Any, run_id: str) -> Dict[str, Any]:
        run = await asyncio.to_thread(self._owned, user.id, run_id)
        progress = None
        if not run.terminal:
            run, progress = await self._reconcile(run)
        return run.to_dict(progress)

    async def cancel(self, user: Any, run_id: str) -> Dict[str, Any]:
        run = await asyncio.to_thread(self._owned, user.id, run_id)
        if run.terminal:
            return run.to_dict()
        if run.generation_id:
            refused = False
            try:
                refused = await self._cancel_generation(run.generation_id, user) is False
            except Exception as failure:
                logger.info("Editor run %s: the generation was not cancelled: %s", run.id, failure)
                refused = True
            if refused:
                generation = await asyncio.to_thread(self.generations.get_by_id, run.generation_id)
                status = getattr(generation, "status", None)
                if status not in TERMINAL_GENERATION:
                    raise DomainError("cancel_failed", "The run could not be cancelled, try again", 400)
                await self._finish(run.id, status)
                current = await asyncio.to_thread(self.repository.find, run.id)
                return current.to_dict()
        settled = await asyncio.to_thread(self.repository.settle, run.id, self.clock(), CANCELLED, None, None)
        current = await asyncio.to_thread(self.repository.find, run.id)
        await self._after_settle(current, settled)
        return current.to_dict()

    def _owned(self, user_id: str, run_id: str) -> EditorRun:
        run = self.repository.get(user_id, run_id)
        if run is None:
            raise NotFoundError("run_not_found", "Run not found")
        return run

    async def _reconcile(self, run: EditorRun) -> Tuple[EditorRun, Optional[float]]:
        if run.generation_id is None:
            return await self._fail(run.id, RESTART_MESSAGE), None
        generation = await asyncio.to_thread(self.generations.get_by_id, run.generation_id)
        if generation is None:
            return await self._fail(run.id, GENERATION_GONE), None
        status = getattr(generation, "status", None)
        if status in TERMINAL_GENERATION:
            await self._finish(run.id, status)
            return await asyncio.to_thread(self.repository.find, run.id), None
        if run.generation_id not in self._live:
            return await self._fail(run.id, RESTART_MESSAGE), None
        if status == "running" and run.status != RUNNING:
            await asyncio.to_thread(self.repository.update, run.id, self.clock(), status=RUNNING)
            run = await asyncio.to_thread(self.repository.find, run.id)
        return run, getattr(generation, "progress", None)

    async def _fail(self, run_id: str, reason: str) -> Optional[EditorRun]:
        try:
            settled = await asyncio.to_thread(self.repository.settle, run_id, self.clock(), FAILED, None, reason)
            current = await asyncio.to_thread(self.repository.find, run_id)
            await self._after_settle(current, settled)
            return current
        except Exception:
            logger.exception("Editor run %s could not be marked failed", run_id)
            return None

    def _prompt(self, offer: ResolvedOffer, prompt: Optional[str]) -> str:
        if offer.via is not None and offer.task.fallback is not None:
            return offer.task.fallback[1]
        text = (prompt or "").strip()
        if offer.task.prompt == "required" and not text:
            raise DomainError("prompt_required", "Describe the change first", 422)
        return text

    def _prepare(self, user: Any, offer: ResolvedOffer, body: StartEditorRunRequest, region_ref: Any) -> PreparedRun:
        with self.media_tools.render_picture(user, body.input, body.steps) as path:
            picture = regions.cap_picture(prepare_image(path).convert("RGB"))
        region = None
        if region_ref is not None:
            with self.media_tools.open_input(user, region_ref) as path:
                with Image.open(path) as opened:
                    region = regions.land_mask(opened, picture.size)
        if offer.task.region == "required" and region is None:
            raise DomainError("region_empty", "Mark the area first", 422)
        declaration = offer.declaration
        array = np.asarray(picture)
        geometry = regions.plan(
            array.shape,
            region,
            declaration.region,
            regions.context_from(body.setup.form_data, declaration.context),
            declaration.size.area,
            declaration.size.multiple,
            declaration.size.buckets,
        )
        cut = regions.prepare(array, region, geometry)
        return PreparedRun(
            geometry=geometry,
            input_png=png_bytes(cut.image),
            mask_png=png_bytes(cut.mask) if cut.mask is not None else None,
            base_png=png_bytes(Image.fromarray(cut.base)),
            region_png=png_bytes(Image.fromarray(cut.region)) if cut.region is not None else None,
        )

    async def _store(self, user_id: str, prepared: PreparedRun, stored: Dict[str, Any]) -> None:
        for name, data in (
            ("input", prepared.input_png),
            ("mask", prepared.mask_png),
            ("base", prepared.base_png),
            ("region", prepared.region_png),
        ):
            stored[name] = None if data is None else await self._put(user_id, f"editor-{name}.png", data, True)
            if stored[name] is not None:
                self._hold(stored[name].id)
        for name in [name for name, item in stored.items() if item is None]:
            del stored[name]

    async def _put(self, user_id: str, filename: str, data: bytes, working: bool = False) -> Any:
        return await self.media_store.upload_media(
            file_data=data,
            filename=filename,
            content_type="image/png",
            user_id=user_id,
            purpose=UPLOAD_PURPOSE_EDITOR_RUN if working else UPLOAD_PURPOSE_DERIVED,
            internal=working,
        )

    def _hold(self, upload_id: Optional[str]) -> None:
        if upload_id:
            self._holds[upload_id] = self._holds.get(upload_id, 0) + 1

    def _release(self, stored: Dict[str, Any]) -> None:
        for item in stored.values():
            count = self._holds.get(item.id, 0) - 1
            if count > 0:
                self._holds[item.id] = count
            else:
                self._holds.pop(item.id, None)

    @staticmethod
    def _entries(stored: Dict[str, Any]) -> List[Tuple[Optional[str], Optional[str]]]:
        return [(item.id, item.filename) for item in stored.values() if item is not None]

    async def _discard(self, user_id: str, entries: List[Tuple[Optional[str], Optional[str]]], exclude: Optional[str]) -> None:
        for upload_id, filename in entries:
            if not filename:
                continue
            try:
                if upload_id and (
                    self._holds.get(upload_id) or await asyncio.to_thread(self.repository.referenced, upload_id, exclude)
                ):
                    continue
                await asyncio.to_thread(self.media_store.delete_upload, filename, user_id)
            except Exception:
                logger.warning("Editor run: the file %s could not be removed", filename, exc_info=True)

    def _form(self, offer: ResolvedOffer, body: StartEditorRunRequest, stored: Dict[str, Any], geometry: Geometry) -> Dict[str, Any]:
        declaration = offer.declaration
        fields = mode_fields(offer.mode)
        form = dict(body.setup.form_data)
        image_name = image_field_name(declaration)
        path = stored["input"].relative_path
        if isinstance(declaration.inputs.image, str):
            form[image_name] = path
        else:
            items = list(form.get(image_name) or [])
            index = declaration.inputs.image.index
            if index < len(items):
                items[index] = path
            else:
                items.append(path)
            form[image_name] = items
        mask_name = declaration.inputs.mask
        if mask_name:
            if stored.get("mask") is not None:
                form[mask_name] = stored["mask"].relative_path
            else:
                form.pop(mask_name, None)
        if declaration.size.field:
            form[declaration.size.field] = f"{geometry.working[0]}x{geometry.working[1]}"
        form.update(declaration.set_values)
        quantity = declaration.quantity_field or derived_quantity_field(offer.mode, fields)
        if quantity:
            form[quantity] = body.candidates
        seed_name = declaration.seed_field or derived_seed_field(fields)
        if body.seed is not None and seed_name:
            form[seed_name] = body.seed
        return form

    async def _tag(self, generation_id: str, origin: Dict[str, Any]) -> bool:
        try:
            return bool(await asyncio.to_thread(self.generations.update_origin, generation_id, origin))
        except Exception:
            logger.exception("Editor run %s: the generation could not be tagged with its origin", origin["run_id"])
            return False

    async def _abort_generation(self, generation_id: str, user: Any) -> None:
        try:
            await self._cancel_generation(generation_id, user)
        except Exception:
            logger.exception("Editor run: generation %s could not be cancelled", generation_id)

    def _forget(self, run: EditorRun) -> None:
        if run.generation_id:
            self._live.pop(run.generation_id, None)

    async def _after_settle(self, run: Optional[EditorRun], settled: bool) -> None:
        if run is None:
            return
        self._forget(run)
        if not settled:
            return
        await self._announce(run)
        await self._cleanup(run)

    async def _cleanup(self, run: EditorRun) -> None:
        crop = run.crop or {}
        names = crop.get("names") or {}
        files = crop.get("files") or {}
        entries = [(files.get(name), filename) for name, filename in names.items()]
        if not entries:
            return
        await self._discard(run.user_id, entries, run.id)
        try:
            await asyncio.to_thread(
                self.repository.update, run.id, self.clock(), crop={**crop, "files": {}, "names": {}}
            )
        except Exception:
            logger.exception("Editor run %s: its working files could not be forgotten", run.id)

    async def _announce(self, run: Optional[EditorRun]) -> None:
        if run is None or not run.generation_id:
            return
        message = {"type": MESSAGE_TYPE, "generation_id": run.generation_id, "run": run.to_dict()}
        try:
            await self.hub.broadcast_to_generation(run.generation_id, message)
        except Exception:
            logger.exception("Editor run %s: the update could not be sent", run.id)

    async def _finish(self, run_id: str, hint: Optional[str] = None) -> None:
        if run_id in self._finishing:
            return
        self._finishing.add(run_id)
        try:
            await self._settle_from_generation(run_id, hint)
        except Exception:
            logger.exception("Editor run %s could not be finished", run_id)
            await self._fail(run_id, NOT_FINISHED)
        finally:
            self._finishing.discard(run_id)

    async def _settle_from_generation(self, run_id: str, hint: Optional[str]) -> None:
        run = await asyncio.to_thread(self.repository.find, run_id)
        if run is None or run.terminal or not run.generation_id:
            return
        generation = await asyncio.to_thread(self.generations.get_by_id, run.generation_id)
        if generation is None:
            outcome = (FAILED, None, GENERATION_GONE)
        else:
            status = getattr(generation, "status", None)
            if status not in TERMINAL_GENERATION:
                status = hint
            if status == "completed":
                outcome = await self._patches(run)
            elif status == "failed":
                outcome = (FAILED, None, getattr(generation, "error_user_message", None) or GENERATION_FAILED)
            elif status == "cancelled":
                outcome = (CANCELLED, None, None)
            else:
                return
        settled = await asyncio.to_thread(self.repository.settle, run.id, self.clock(), *outcome)
        await self._after_settle(await asyncio.to_thread(self.repository.find, run.id), settled)

    async def _patches(self, run: EditorRun) -> Tuple[str, Optional[List[Dict[str, Any]]], Optional[str]]:
        candidates: List[Dict[str, Any]] = []
        try:
            rendered = await asyncio.to_thread(self._render, run)
            if not rendered:
                return FAILED, None, NO_PICTURE
            for data, file_id, seed in rendered:
                stored = await self._put(run.user_id, "editor-patch.png", data)
                candidates.append(
                    {"patch": {"item_id": stored.id}, "url": stored.url, "seed": seed, "generation_file_id": file_id}
                )
            return DONE, candidates, None
        except Exception:
            logger.exception("Editor run %s: stitching failed", run.id)
            return FAILED, None, NOT_PLACED

    def _render(self, run: EditorRun) -> List[Tuple[bytes, str, Optional[int]]]:
        files = self.generations.get_files(run.generation_id, is_final=True, file_type="IMAGE")
        if not files:
            files = self.generations.get_files(run.generation_id, file_type="IMAGE")
        if not files:
            return []
        geometry = Geometry.from_dict(run.crop)
        held = run.crop.get("files") or {}
        base = self._load(run.user_id, held["base"], "RGB")
        region = self._load(run.user_id, held.get("region"), "L")
        seeds = self._seeds(run.generation_id)
        rendered = []
        for index, record in enumerate(files[:MAX_CANDIDATES]):
            with self.media_tools.open_input(run.user_id, {"generation_file_id": record.id}) as path:
                with Image.open(path) as opened:
                    result = opened.convert("RGB")
            patch = regions.stitch(base, region, result, geometry)
            rendered.append((png_bytes(Image.fromarray(patch)), record.id, seeds.get(index)))
        return rendered

    def _load(self, user_id: str, upload_id: Optional[str], mode: str) -> Optional[np.ndarray]:
        if not upload_id:
            return None
        with self.media_tools.open_input(user_id, {"item_id": upload_id}) as path:
            with Image.open(path) as opened:
                return np.asarray(opened.convert(mode)).copy()

    def _seeds(self, generation_id: str) -> Dict[int, int]:
        try:
            rows = self.parameters.get_by_name(generation_id, "seed")
            return {int(row.parameter_index): int(json.loads(row.parameter_value)) for row in rows}
        except Exception:
            return {}
