import asyncio
import io
import json
import logging
import mimetypes
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import numpy as np
from PIL import Image

from src.features.editor_tasks import clips, expand as expand_ops, parts, regions, upscale
from src.features.editor_tasks.dto import MAX_CANDIDATES, StartEditorRunRequest
from src.features.editor_tasks.manager import EditorTaskManager, ResolvedOffer, scale_limits
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
from src.features.presets.editor_modes import clamp_strength
from src.features.presets.extensions import walk_fields
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
NO_VIDEO = "The model returned no video"
NOT_PLACED = "The result could not be placed on the picture"
NOT_SPLICED = "The result could not be placed in the clip"
RANGE_KIND = "range"
EXPAND_KIND = "expand"
CLIP_TYPE = "video/mp4"
GENERATION_FAILED = "The generation failed"
GENERATION_GONE = "The generation could not be found"
NOT_FINISHED = "The run could not be finished"
TERMINAL_GENERATION = frozenset({"completed", "failed", "cancelled"})
PNG_LEVEL = 1
ORIGIN_KIND = "editor"
PROMPT_SLOT = "{prompt}"
PARTS_KIND = "parts"
COMPOSE_REGIONS = "regions"
COMPOSE_TILES = "tiles"
PART_FAILED = "A part of the run could not be started"

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
    geometry: Optional[Geometry]
    input_png: Optional[bytes]
    mask_png: Optional[bytes]
    base_png: Optional[bytes]
    region_png: Optional[bytes]
    window: Optional[Dict[str, Any]] = None
    clip_input: Optional[bytes] = None
    clip_base: Optional[bytes] = None
    clip_suffix: str = ".mp4"
    base_suffix: str = ".mp4"
    canvas_png: Optional[bytes] = None
    chain: Optional[Dict[str, Any]] = None


@dataclass
class ExpandChain:
    user: Any
    offer: ResolvedOffer
    body: StartEditorRunRequest
    prompt: str


@dataclass
class PartsPlan:
    compose: str
    items: List[Dict[str, Any]]
    base_png: bytes
    region_png: Optional[bytes]
    picture: Tuple[int, int]
    rect: Dict[str, float]
    soft: bool = False
    output: Optional[Tuple[int, int]] = None
    scale: Optional[float] = None


@dataclass
class PartsJob:
    user: Any
    offer: ResolvedOffer
    body: StartEditorRunRequest
    prompt: str
    origin: Dict[str, Any]


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
        self._chains: Dict[str, ExpandChain] = {}
        self._finishing: Set[str] = set()
        self._holds: Dict[str, int] = {}
        self._jobs: Dict[str, PartsJob] = {}
        self._cancelled: Set[str] = set()
        self._rerun: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
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
                self._schedule(run_id, data.get("status"), data.get("generation_id"))
        except Exception:
            logger.exception("Editor runs: the completion hook failed")
        return context

    def _schedule(self, run_id: str, hint: Optional[str] = None, generation_id: Optional[str] = None) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._finish(run_id, hint, generation_id), loop)

    async def start(self, user: Any, task_id: str, body: StartEditorRunRequest) -> Dict[str, Any]:
        self._loop = asyncio.get_running_loop()
        offer = await asyncio.to_thread(
            self.tasks.resolve_offer, user, task_id, body.setup.preset_id, body.setup.mode
        )
        await asyncio.to_thread(self.edits.get, user.id, body.edit.id)
        self._check_range(offer, body)
        self._check_expand(offer, body)
        prompt = self._prompt(offer, body.prompt)
        region_ref = body.region if offer.task.region != "none" else None
        if offer.task.region == "required" and region_ref is None:
            raise DomainError("region_required", "Mark the area first", 422)
        self._check_scale(offer, body)
        if offer.task.plan != "single" and "video" not in offer.task.editors:
            return await self._start_parts(user, task_id, offer, body, prompt, region_ref)
        if offer.task.grows:
            prepared = await asyncio.to_thread(self._prepare_expand, user, offer, body)
        elif "video" in offer.task.editors:
            prepared = await asyncio.to_thread(self._prepare_clip, user, offer, body)
        else:
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
            request = self._request(offer, body, prompt, stored, prepared)
            run = EditorRun(
                id=run_id,
                user_id=user.id,
                task=task_id,
                edit_id=body.edit.id,
                step_id=body.edit.step_id,
                rect=prepared.geometry.rect() if prepared.geometry is not None else None,
                crop={
                    **(prepared.geometry.to_dict() if prepared.geometry is not None else prepared.window),
                    **(prepared.chain or {}),
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
        if offer.task.grows:
            self._chains[run_id] = ExpandChain(user, offer, body, prompt)
        await asyncio.to_thread(self.repository.update, run_id, self.clock(), generation_id=generation_id)
        current = await asyncio.to_thread(self.repository.find, run_id)
        await self._announce(current)
        generation = await asyncio.to_thread(self.generations.get_by_id, generation_id)
        status = getattr(generation, "status", None)
        if status in TERMINAL_GENERATION:
            self._schedule(run_id, status, generation_id)
        return current.to_dict()

    async def get(self, user: Any, run_id: str) -> Dict[str, Any]:
        run = await asyncio.to_thread(self._owned, user.id, run_id)
        progress = None
        if not run.terminal:
            run, progress = await self._reconcile(run)
        return run.to_dict(self._overall(run, progress))

    @staticmethod
    def _overall(run: Optional[EditorRun], progress: Optional[float]) -> Optional[float]:
        crop = (run.crop if run is not None else None) or {}
        passes = crop.get("passes")
        if progress is None or crop.get("kind") != EXPAND_KIND or not passes:
            return progress
        return (int(crop.get("index") or 0) + min(1.0, max(0.0, float(progress)))) / len(passes)

    async def cancel(self, user: Any, run_id: str) -> Dict[str, Any]:
        run = await asyncio.to_thread(self._owned, user.id, run_id)
        if run.terminal:
            return run.to_dict()
        current = self._current(run)
        if (run.crop or {}).get("parts") is not None:
            self._cancelled.add(run.id)
        if current:
            refused = False
            try:
                refused = await self._cancel_generation(current, user) is False
            except Exception as failure:
                logger.info("Editor run %s: the generation was not cancelled: %s", run.id, failure)
                refused = True
            if refused:
                generation = await asyncio.to_thread(self.generations.get_by_id, current)
                status = getattr(generation, "status", None)
                if status not in TERMINAL_GENERATION:
                    self._cancelled.discard(run.id)
                    raise DomainError("cancel_failed", "The run could not be cancelled, try again", 400)
                await self._finish(run.id, status, current)
                current_run = await asyncio.to_thread(self.repository.find, run.id)
                return current_run.to_dict()
        settled = await asyncio.to_thread(self.repository.settle, run.id, self.clock(), CANCELLED, None, None)
        latest = await asyncio.to_thread(self.repository.find, run.id)
        await self._after_settle(latest, settled)
        return latest.to_dict()

    @staticmethod
    def _current(run: EditorRun) -> Optional[str]:
        parts_state = (run.crop or {}).get("parts")
        if parts_state is not None:
            return parts_state.get("generation_id")
        return run.generation_id

    @staticmethod
    def _progress(run: EditorRun, value: Optional[float]) -> Optional[float]:
        parts_state = (run.crop or {}).get("parts")
        if parts_state is None:
            return value
        total = int(parts_state["total"])
        return min(1.0, (int(parts_state["done"]) + min(1.0, max(0.0, value or 0.0))) / total)

    def _owned(self, user_id: str, run_id: str) -> EditorRun:
        run = self.repository.get(user_id, run_id)
        if run is None:
            raise NotFoundError("run_not_found", "Run not found")
        return run

    async def _reconcile(self, run: EditorRun) -> Tuple[EditorRun, Optional[float]]:
        current = self._current(run)
        if current is None:
            return await self._fail(run.id, RESTART_MESSAGE), None
        generation = await asyncio.to_thread(self.generations.get_by_id, current)
        if generation is None:
            return await self._fail(run.id, GENERATION_GONE), None
        status = getattr(generation, "status", None)
        if status in TERMINAL_GENERATION:
            await self._finish(run.id, status, current)
            return await asyncio.to_thread(self.repository.find, run.id), None
        if current not in self._live:
            return await self._fail(run.id, RESTART_MESSAGE), None
        if status == "running" and run.status != RUNNING:
            await asyncio.to_thread(self.repository.update, run.id, self.clock(), status=RUNNING)
            run = await asyncio.to_thread(self.repository.find, run.id)
        return run, self._progress(run, getattr(generation, "progress", None))

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
        text = (prompt or "").strip()
        task = offer.task
        if task.fallback is not None and task.prompt == "optional":
            return text or task.fallback[1]
        if offer.via is not None and task.fallback is not None:
            wording = task.fallback[1]
            if PROMPT_SLOT in wording:
                if not text:
                    raise DomainError("prompt_required", "Describe the change first", 422)
                return wording.replace(PROMPT_SLOT, text)
            return wording
        if task.prompt == "required" and not text:
            raise DomainError("prompt_required", "Describe the change first", 422)
        if task.prompt == "none" and offer.spec.instruction:
            return offer.spec.instruction
        return text

    @staticmethod
    def _check_scale(offer: ResolvedOffer, body: StartEditorRunRequest) -> None:
        if offer.task.plan != "tiles" and (body.scale is not None or body.target is not None):
            raise DomainError("scale_not_supported", "This task takes no scale or target size", 422)

    @staticmethod
    def _check_range(offer: ResolvedOffer, body: StartEditorRunRequest) -> None:
        if body.range is not None and offer.task.range == "none":
            raise DomainError("range_not_supported", "This task takes no time range", 422)
        if body.range is None and offer.task.range == "required":
            raise DomainError("range_required", "Mark the part of the clip first", 422)

    @staticmethod
    def _check_expand(offer: ResolvedOffer, body: StartEditorRunRequest) -> None:
        if offer.task.grows and body.expand is None:
            raise DomainError("expand_required", "Choose the new size first", 422)
        if body.expand is not None and not offer.task.grows:
            raise DomainError("expand_not_supported", "This task does not change the canvas", 422)

    @staticmethod
    def _check_cap(offer: ResolvedOffer, window: "clips.FrameRange") -> None:
        cap = offer.spec.max_range_s
        if cap is None:
            return
        if window.end_s - window.start_s > cap + 0.5 / window.fps:
            raise DomainError(
                "range_too_long",
                f"{offer.task.label} works on up to {cap:g} s; mark a shorter section",
                422,
            )

    def _prepare_clip(self, user: Any, offer: ResolvedOffer, body: StartEditorRunRequest) -> PreparedRun:
        with self.media_tools.render_clip(user, body.input, body.steps) as path:
            found = clips.facts_of(path)
            asked = body.range
            window = clips.plan_range(found, asked.start_s if asked else None, asked.end_s if asked else None)
            self._check_cap(offer, window)
            whole = window.start == 0 and window.end >= found.frames
            suffix = path.suffix.lower() or ".mp4"
            base = path.read_bytes()
            if whole:
                cut = None
            else:
                with tempfile.TemporaryDirectory() as scratch:
                    cut = clips.cut(path, found, window, Path(scratch) / "cut.mp4").read_bytes()
        return PreparedRun(
            geometry=None,
            input_png=None,
            mask_png=None,
            base_png=None,
            region_png=None,
            window={
                "kind": RANGE_KIND,
                "whole": whole,
                "start_frame": window.start,
                "end_frame": window.end,
                "start_s": round(window.start_s, 6),
                "end_s": round(window.end_s, 6),
                "frames": found.frames,
                "fps": found.fps,
                "width": found.width,
                "height": found.height,
                "duration": found.duration,
                "has_audio": found.has_audio,
                "adopt_size": bool(offer.task.resizes and whole),
            },
            clip_input=base if cut is None else cut,
            clip_base=None if cut is None else base,
            clip_suffix=suffix if cut is None else ".mp4",
            base_suffix=suffix,
        )

    def _prepare(self, user: Any, offer: ResolvedOffer, body: StartEditorRunRequest, region_ref: Any) -> PreparedRun:
        with self.media_tools.render_picture(user, body.input, body.steps) as path:
            picture = regions.cap_picture(prepare_image(path).convert("RGB"))
        region = None
        task = offer.task
        land = regions.land_matte if task.matte else regions.land_mask
        if region_ref is not None:
            with self.media_tools.open_input(user, region_ref) as path:
                with Image.open(path) as opened:
                    region = land(opened, picture.size)
        if task.region == "required" and region is None:
            raise DomainError("region_empty", "Mark the area first", 422)
        if task.grow > 0:
            shadow = task.id == "remove_object" and body.options.get("include_shadow") is True
            region = regions.grow_region(region, task.grow, shadow)
        spec = offer.spec
        array = np.asarray(picture)
        geometry = regions.plan(
            array.shape,
            region,
            spec.region,
            regions.context_from(body.setup.form_data, spec.context),
            spec.size["area"],
            spec.size["multiple"],
            spec.size.get("buckets"),
            feather=task.feather,
            matte=task.matte,
        )
        cut = regions.prepare(array, region, geometry, spec.region == "crop" and bool(spec.inputs.get("mask")))
        return PreparedRun(
            geometry=geometry,
            input_png=png_bytes(cut.image),
            mask_png=png_bytes(cut.mask) if cut.mask is not None else None,
            base_png=png_bytes(Image.fromarray(cut.base)),
            region_png=png_bytes(Image.fromarray(cut.region)) if cut.region is not None else None,
        )

    def _expand_inputs(
        self, offer: ResolvedOffer, canvas_rgb: np.ndarray, step: expand_ops.Pass
    ) -> Tuple[Geometry, Image.Image, Optional[Image.Image]]:
        spec = offer.spec
        width, height = step.size
        has_mask = bool(spec.inputs.get("mask"))
        strategy = "mask" if has_mask and spec.region == "mask" else "whole"
        window = expand_ops.pass_input(canvas_rgb, step)
        soft = expand_ops.soft_mask((height, width), step.known_local(), expand_ops.overlap_for(step.size))
        geometry = regions.plan(
            window.shape,
            soft,
            strategy,
            0.0,
            spec.size["area"],
            spec.size["multiple"],
            spec.size.get("buckets"),
        )
        geometry = replace(
            geometry, box=(0, height, 0, width), whole=True, has_region=True, feather_px=expand_ops.overlap_for(step.size)
        )
        if strategy == "mask":
            return geometry, Image.fromarray(window), Image.fromarray(soft) if has_mask else None
        work_w, work_h = geometry.working
        image = Image.fromarray(regions.resample_image(window, work_w, work_h))
        mask = Image.fromarray(regions.resample_image(soft, work_w, work_h)) if has_mask else None
        return geometry, image, mask

    def _prepare_expand(self, user: Any, offer: ResolvedOffer, body: StartEditorRunRequest) -> PreparedRun:
        with self.media_tools.render_picture(user, body.input, body.steps) as path:
            picture = regions.cap_picture(prepare_image(path).convert("RGB"))
        asked = body.expand
        try:
            canvas = expand_ops.make_canvas(picture.size, asked.width, asked.height, asked.x, asked.y)
        except expand_ops.ExpandError as failure:
            raise DomainError(failure.code, failure.message, 422)
        passes = expand_ops.plan_passes(canvas)
        source = np.asarray(picture)
        canvas_rgb = expand_ops.blank_canvas(source, canvas)
        geometry, image, mask = self._expand_inputs(offer, canvas_rgb, passes[0])
        return PreparedRun(
            geometry=geometry,
            input_png=png_bytes(image),
            mask_png=png_bytes(mask) if mask is not None else None,
            base_png=png_bytes(picture),
            region_png=None,
            canvas_png=png_bytes(Image.fromarray(canvas_rgb)),
            chain={
                "kind": EXPAND_KIND,
                "canvas": canvas.to_dict(),
                "passes": [step.to_dict() for step in passes],
                "index": 0,
            },
        )

    async def _expand(self, run: EditorRun) -> Optional[Tuple[str, Optional[List[Dict[str, Any]]], Optional[str]]]:
        chain = self._chains.get(run.id)
        if chain is None:
            return FAILED, None, RESTART_MESSAGE
        try:
            files = self.generations.get_files(run.generation_id, is_final=True, file_type="IMAGE")
            if not files:
                files = self.generations.get_files(run.generation_id, file_type="IMAGE")
            if not files:
                return FAILED, None, NO_PICTURE
            canvas_rgb = await asyncio.to_thread(self._expand_stitch, run, files[0])
            crop = run.crop or {}
            index = int(crop["index"])
            if index + 1 < len(crop["passes"]):
                await self._expand_next(run, chain, canvas_rgb, index + 1)
                return None
            canvas = expand_ops.Canvas.from_dict(crop["canvas"])
            patch = png_bytes(Image.fromarray(expand_ops.patch_of(canvas_rgb, canvas)))
            stored = await self._put(run.user_id, "editor-patch.png", patch)
            seed = self._seeds(run.generation_id).get(0)
            return DONE, [{"patch": {"item_id": stored.id}, "url": stored.url, "seed": seed, "generation_file_id": files[0].id}], None
        except Exception:
            logger.exception("Editor run %s: expanding failed", run.id)
            return FAILED, None, NOT_PLACED

    def _expand_stitch(self, run: EditorRun, record: Any) -> np.ndarray:
        crop = run.crop or {}
        step = expand_ops.Pass.from_dict(crop["passes"][int(crop["index"])])
        canvas_rgb = self._load(run.user_id, (crop.get("files") or {}).get("canvas"), "RGB")
        base = expand_ops.pass_input(canvas_rgb, step)
        with self.media_tools.open_input(run.user_id, {"generation_file_id": record.id}) as path:
            with Image.open(path) as opened:
                result = opened.convert("RGB")
        expand_ops.place(canvas_rgb, expand_ops.stitch_pass(base, result, step), step)
        return canvas_rgb

    async def _expand_next(self, run: EditorRun, chain: ExpandChain, canvas_rgb: np.ndarray, index: int) -> None:
        crop = dict(run.crop or {})
        step = expand_ops.Pass.from_dict(crop["passes"][index])
        geometry, image, mask = await asyncio.to_thread(self._expand_inputs, chain.offer, canvas_rgb, step)
        prepared = PreparedRun(
            geometry=geometry,
            input_png=png_bytes(image),
            mask_png=png_bytes(mask) if mask is not None else None,
            base_png=None,
            region_png=None,
            canvas_png=png_bytes(Image.fromarray(canvas_rgb)),
        )
        stored: Dict[str, Any] = {}
        released = False
        try:
            await self._store(run.user_id, prepared, stored)
            files = dict(crop.get("files") or {})
            names = dict(crop.get("names") or {})
            replaced = [(files.get(name), names.get(name)) for name in ("input", "mask", "canvas")]
            for name in ("input", "mask", "canvas"):
                files.pop(name, None)
                names.pop(name, None)
            for name, item in stored.items():
                files[name] = item.id
                names[name] = item.filename
            request = self._request(chain.offer, chain.body, chain.prompt, stored, prepared)
            origin = {
                "kind": ORIGIN_KIND,
                "edit_id": run.edit_id,
                "step_id": run.step_id,
                "task": run.task,
                "run_id": run.id,
            }
            submitted = await self._submit(request, chain.user)
            generation_id = submitted["generation_id"]
            if not await self._tag(generation_id, origin):
                await self._abort_generation(generation_id, chain.user)
                raise DomainError("run_start_failed", "The run could not be started, try again", 500)
            crop.update({**geometry.to_dict(), "index": index, "files": files, "names": names})
            previous = run.generation_id
            await asyncio.to_thread(
                self.repository.update, run.id, self.clock(), generation_id=generation_id, crop=crop, status=RUNNING
            )
            self._live[generation_id] = run.id
            self._live.pop(previous, None)
            self._release(stored)
            released = True
        except BaseException:
            if not released:
                self._release(stored)
            await self._discard(run.user_id, self._entries(stored), run.id)
            raise
        await self._discard(run.user_id, replaced, run.id)
        await self._announce(await asyncio.to_thread(self.repository.find, run.id))

    async def _store(self, user_id: str, prepared: PreparedRun, stored: Dict[str, Any]) -> None:
        if prepared.window is not None:
            await self._store_clip(user_id, prepared, stored)
            return
        for name, data in (
            ("input", prepared.input_png),
            ("mask", prepared.mask_png),
            ("base", prepared.base_png),
            ("region", prepared.region_png),
            ("canvas", prepared.canvas_png),
        ):
            stored[name] = None if data is None else await self._put(user_id, f"editor-{name}.png", data, True)
            if stored[name] is not None:
                self._hold(stored[name].id)
        for name in [name for name, item in stored.items() if item is None]:
            del stored[name]

    async def _store_clip(self, user_id: str, prepared: PreparedRun, stored: Dict[str, Any]) -> None:
        kind = mimetypes.guess_type(f"x{prepared.clip_suffix}")[0] or CLIP_TYPE
        stored["input"] = await self._put(
            user_id, f"editor-input{prepared.clip_suffix}", prepared.clip_input, True, kind
        )
        self._hold(stored["input"].id)
        if prepared.clip_base is None:
            stored["base"] = stored["input"]
        else:
            base_kind = mimetypes.guess_type(f"x{prepared.base_suffix}")[0] or CLIP_TYPE
            stored["base"] = await self._put(
                user_id, f"editor-base{prepared.base_suffix}", prepared.clip_base, True, base_kind
            )
        self._hold(stored["base"].id)

    async def _put(
        self, user_id: str, filename: str, data: bytes, working: bool = False, content_type: str = "image/png"
    ) -> Any:
        return await self.media_store.upload_media(
            file_data=data,
            filename=filename,
            content_type=content_type,
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
        seen: Set[str] = set()
        for upload_id, filename in entries:
            if not filename or filename in seen:
                continue
            seen.add(filename)
            try:
                if upload_id and (
                    self._holds.get(upload_id) or await asyncio.to_thread(self.repository.referenced, upload_id, exclude)
                ):
                    continue
                await asyncio.to_thread(self.media_store.delete_upload, filename, user_id)
            except Exception:
                logger.warning("Editor run: the file %s could not be removed", filename, exc_info=True)

    def _request(
        self, offer: ResolvedOffer, body: StartEditorRunRequest, prompt: str, stored: Dict[str, Any], prepared: PreparedRun
    ) -> GenerationRequest:
        request = GenerationRequest(
            preset_id=body.setup.preset_id,
            prompt=prompt,
            mode=offer.mode_name,
            form_data=self._form(offer, body, stored, prepared.geometry, count=1 if offer.task.grows else None),
        )
        request._editor_run = True
        return request

    def _form(
        self,
        offer: ResolvedOffer,
        body: StartEditorRunRequest,
        stored: Dict[str, Any],
        geometry: Optional[Geometry],
        scale: Optional[float] = None,
        output: Optional[Tuple[int, int]] = None,
        count: Optional[int] = None,
    ) -> Dict[str, Any]:
        spec = offer.spec
        form = dict(body.setup.form_data)
        source = spec.inputs["source"]
        path = stored["input"].relative_path
        if isinstance(source, str):
            form[source] = path
        else:
            items = list(form.get(source["field"]) or [])
            items.insert(min(int(source.get("index") or 0), len(items)), path)
            form[source["field"]] = items
        mask_name = spec.inputs.get("mask")
        if mask_name:
            if stored.get("mask") is not None:
                form[mask_name] = stored["mask"].relative_path
            else:
                form.pop(mask_name, None)
        size_field = spec.size.get("field")
        if size_field and geometry is not None:
            form[size_field] = f"{geometry.working[0]}x{geometry.working[1]}"
        elif size_field and output is not None:
            form[size_field] = f"{output[0]}x{output[1]}"
        scale_field = spec.inputs.get("scale")
        if scale_field and scale is not None:
            form[scale_field] = scale
        form.update(spec.fixed)
        strength = clamp_strength(spec, body.strength)
        if strength is not None:
            form[strength[0]] = strength[1]
        quantity = spec.inputs.get("quantity")
        if quantity:
            form[quantity] = count if count is not None else self._count(form.get(quantity), body.candidates)
        seed_name = spec.inputs.get("seed") or self._seed_field(offer)
        if body.seed is not None and seed_name:
            form[seed_name] = body.seed
        return form

    @staticmethod
    def _count(value: Any, fallback: int) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            return fallback
        return max(1, min(MAX_CANDIDATES, int(value)))

    @staticmethod
    def _seed_field(offer: ResolvedOffer) -> Optional[str]:
        for form in offer.mode.forms:
            for field, _ in walk_fields(form.fields):
                if field.type == "seed" and field.name:
                    return field.name
        return None

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
        self._chains.pop(run.id, None)
        self._jobs.pop(run.id, None)
        self._cancelled.discard(run.id)
        self._rerun.pop(run.id, None)
        for generation_id in [key for key, value in self._live.items() if value == run.id]:
            del self._live[generation_id]

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

    async def _start_parts(
        self, user: Any, task_id: str, offer: ResolvedOffer, body: StartEditorRunRequest, prompt: str, region_ref: Any
    ) -> Dict[str, Any]:
        plan = await asyncio.to_thread(self._plan_parts, user, offer, body, region_ref)
        run_id = ID_PREFIX + self.new_id()
        stored: Dict[str, Any] = {}
        released = False
        try:
            await self._store_parts_base(user.id, plan, stored)
            run = EditorRun(
                id=run_id,
                user_id=user.id,
                task=task_id,
                edit_id=body.edit.id,
                step_id=body.edit.step_id,
                rect=plan.rect,
                crop={
                    "kind": PARTS_KIND,
                    "compose": plan.compose,
                    "picture": list(plan.picture),
                    "output": list(plan.output) if plan.output else None,
                    "scale": plan.scale,
                    "soft": plan.soft,
                    "files": {name: item.id for name, item in stored.items()},
                    "names": {name: item.filename for name, item in stored.items()},
                    "parts": {"total": len(plan.items), "done": 0, "generation_id": None, "items": plan.items},
                },
            )
            await asyncio.to_thread(self.repository.create, run, self.clock())
            self._release(stored)
            released = True
            job = PartsJob(
                user=user,
                offer=offer,
                body=body,
                prompt=prompt,
                origin={
                    "kind": ORIGIN_KIND,
                    "edit_id": body.edit.id,
                    "step_id": body.edit.step_id,
                    "task": task_id,
                    "run_id": run_id,
                },
            )
            self._jobs[run_id] = job
            try:
                generation_id = await self._submit_part(run_id, job, 0)
            except BaseException:
                self._jobs.pop(run_id, None)
                await asyncio.to_thread(self.repository.delete, run_id)
                raise
        except BaseException:
            if not released:
                self._release(stored)
            await self._discard(user.id, self._entries(stored), run_id)
            raise
        await asyncio.to_thread(self.repository.update, run_id, self.clock(), generation_id=generation_id)
        current = await asyncio.to_thread(self.repository.find, run_id)
        await self._announce(current)
        generation = await asyncio.to_thread(self.generations.get_by_id, generation_id)
        status = getattr(generation, "status", None)
        if status in TERMINAL_GENERATION:
            self._schedule(run_id, status, generation_id)
        return current.to_dict()

    def _plan_parts(self, user: Any, offer: ResolvedOffer, body: StartEditorRunRequest, region_ref: Any) -> PartsPlan:
        if offer.task.plan == "tiles":
            return self._plan_tiles(user, offer, body)
        with self.media_tools.render_picture(user, body.input, body.steps) as path:
            picture = regions.cap_picture(prepare_image(path).convert("RGB"))
        with self.media_tools.open_input(user, region_ref) as path:
            with Image.open(path) as opened:
                region = regions.land_mask(opened, picture.size)
        if region is None:
            raise DomainError("region_empty", "Mark the area first", 422)
        found = parts.Components(region)
        if found.count > parts.MAX_PARTS:
            raise DomainError("too_many_regions", f"Mark at most {parts.MAX_PARTS} separate areas", 422)
        spec = offer.spec
        array = np.asarray(picture)
        context = regions.context_from(body.setup.form_data, spec.context)
        geometries = [
            regions.plan(
                array.shape,
                found.mask(index),
                spec.region,
                context,
                spec.size["area"],
                spec.size["multiple"],
                spec.size.get("buckets"),
            )
            for index in range(found.count)
        ]
        union = parts.union_box([geometry.box for geometry in geometries])
        width, height = picture.size
        return PartsPlan(
            compose=COMPOSE_REGIONS,
            items=[{"geometry": geometry.to_dict()} for geometry in geometries],
            base_png=png_bytes(picture),
            region_png=png_bytes(Image.fromarray(region)),
            picture=(width, height),
            rect={
                "x": union[2] / width,
                "y": union[0] / height,
                "width": (union[3] - union[2]) / width,
                "height": (union[1] - union[0]) / height,
            },
            soft=any(geometry.soft for geometry in geometries),
        )

    def _plan_tiles(self, user: Any, offer: ResolvedOffer, body: StartEditorRunRequest) -> PartsPlan:
        with self.media_tools.render_picture(user, body.input, body.steps) as path:
            picture = prepare_image(path).convert("RGB")
        target = (body.target.width, body.target.height) if body.target is not None else None
        output, derived = upscale.output_size(picture.size, body.scale, target)
        factor = round(body.scale if body.scale is not None else derived, 4)
        limits = scale_limits(offer.mode, offer.spec)
        if limits is not None and not limits[0] <= factor <= limits[1]:
            raise DomainError(
                "upscale_scale_unsupported",
                f"{offer.task.label} with this setup scales by {limits[0]:g} to {limits[1]:g}",
                422,
            )
        tiles = upscale.plan_tiles(picture.size, tile_area=offer.spec.size["area"], output=output)
        return PartsPlan(
            compose=COMPOSE_TILES,
            items=[{"tile": tile.to_dict()} for tile in tiles.tiles],
            base_png=png_bytes(picture),
            region_png=None,
            picture=picture.size,
            rect={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            output=output,
            scale=factor,
        )

    async def _store_parts_base(self, user_id: str, plan: PartsPlan, stored: Dict[str, Any]) -> None:
        for name, data in (("base", plan.base_png), ("region", plan.region_png)):
            if data is None:
                continue
            stored[name] = await self._put(user_id, f"editor-{name}.png", data, True)
            self._hold(stored[name].id)

    def _part_inputs(
        self, run: EditorRun, index: int
    ) -> Tuple[PreparedRun, Optional[Geometry], Optional[Tuple[int, int]]]:
        crop = run.crop or {}
        item = crop["parts"]["items"][index]
        base = self._load(run.user_id, crop["files"]["base"], "RGB")
        if crop["compose"] == COMPOSE_TILES:
            tile = upscale.Tile.from_dict(item["tile"])
            top, bottom, left, right = tile.source
            prepared = PreparedRun(
                geometry=None,
                input_png=png_bytes(Image.fromarray(base[top:bottom, left:right])),
                mask_png=None,
                base_png=None,
                region_png=None,
            )
            return prepared, None, tile.output_size
        geometry = Geometry.from_dict(item["geometry"])
        region = self._load(run.user_id, crop["files"]["region"], "L")
        spec = self._jobs[run.id].offer.spec
        with_mask = spec.region == "crop" and bool(spec.inputs.get("mask"))
        cut = regions.prepare(base, parts.Components(region).mask(index), geometry, with_mask)
        prepared = PreparedRun(
            geometry=geometry,
            input_png=png_bytes(cut.image),
            mask_png=png_bytes(cut.mask) if cut.mask is not None else None,
            base_png=None,
            region_png=None,
        )
        return prepared, geometry, None

    async def _submit_part(self, run_id: str, job: PartsJob, index: int) -> str:
        run = await asyncio.to_thread(self.repository.find, run_id)
        prepared, geometry, output = await asyncio.to_thread(self._part_inputs, run, index)
        user_id = job.user.id
        stored: Dict[str, Any] = {}
        released = False
        keys: List[str] = []
        try:
            await self._store(user_id, prepared, stored)
            keys = [f"part{index}.{name}" for name in stored]
            crop = dict(run.crop)
            crop["files"] = {**crop["files"], **{f"part{index}.{name}": item.id for name, item in stored.items()}}
            crop["names"] = {**crop["names"], **{f"part{index}.{name}": item.filename for name, item in stored.items()}}
            await asyncio.to_thread(self.repository.update, run_id, self.clock(), crop=crop)
            self._release(stored)
            released = True
            request = GenerationRequest(
                preset_id=job.body.setup.preset_id,
                prompt=job.prompt,
                mode=job.offer.mode_name,
                form_data=self._form(job.offer, job.body, stored, geometry, crop.get("scale"), output),
            )
            request._editor_run = True
            submitted = await self._submit(request, job.user)
            generation_id = submitted["generation_id"]
            if not await self._tag(generation_id, job.origin):
                await self._abort_generation(generation_id, job.user)
                raise DomainError("run_start_failed", "The run could not be started, try again", 500)
        except BaseException:
            if not released:
                self._release(stored)
            await self._discard(user_id, self._entries(stored), run_id)
            await self._drop_part_files(run_id, keys)
            raise
        latest = await asyncio.to_thread(self.repository.find, run_id)
        crop = dict(latest.crop)
        state = dict(crop["parts"])
        state["generation_id"] = generation_id
        state["items"] = [
            {**item, "generation_id": generation_id} if position == index else item
            for position, item in enumerate(state["items"])
        ]
        crop["parts"] = state
        self._live[generation_id] = run_id
        await asyncio.to_thread(self.repository.update, run_id, self.clock(), crop=crop)
        return generation_id

    async def _drop_part_files(self, run_id: str, keys: List[str]) -> None:
        if not keys:
            return
        try:
            latest = await asyncio.to_thread(self.repository.find, run_id)
            if latest is None:
                return
            crop = dict(latest.crop)
            crop["files"] = {name: value for name, value in crop["files"].items() if name not in keys}
            crop["names"] = {name: value for name, value in crop["names"].items() if name not in keys}
            await asyncio.to_thread(self.repository.update, run_id, self.clock(), crop=crop)
        except Exception:
            logger.exception("Editor run %s: its part files could not be forgotten", run_id)

    async def _settle_parts(self, run: EditorRun, hint: Optional[str], hinted: Optional[str]) -> None:
        while True:
            current = self._current(run)
            if not current:
                return
            generation = await asyncio.to_thread(self.generations.get_by_id, current)
            if generation is None:
                outcome = (FAILED, None, GENERATION_GONE)
                break
            status = getattr(generation, "status", None)
            if status not in TERMINAL_GENERATION and hinted in (None, current):
                status = hint
            hint = hinted = None
            if status == "completed":
                state = run.crop["parts"]
                if int(state["done"]) + 1 >= int(state["total"]):
                    outcome = await self._compose_parts(run)
                    break
                outcome = await self._advance_parts(run)
                if outcome is not None:
                    break
                run = await asyncio.to_thread(self.repository.find, run.id)
                if run is None or run.terminal:
                    return
                continue
            if status == "failed":
                outcome = (FAILED, None, getattr(generation, "error_user_message", None) or GENERATION_FAILED)
            elif status == "cancelled":
                outcome = (CANCELLED, None, None)
            else:
                return
            break
        settled = await asyncio.to_thread(self.repository.settle, run.id, self.clock(), *outcome)
        await self._after_settle(await asyncio.to_thread(self.repository.find, run.id), settled)

    async def _advance_parts(
        self, run: EditorRun
    ) -> Optional[Tuple[str, Optional[List[Dict[str, Any]]], Optional[str]]]:
        job = self._jobs.get(run.id)
        if job is None:
            return FAILED, None, RESTART_MESSAGE
        if run.id in self._cancelled:
            return CANCELLED, None, None
        finished = int(run.crop["parts"]["done"])
        await self._finish_part(run, finished)
        try:
            generation_id = await self._submit_part(run.id, job, finished + 1)
        except Exception as failure:
            logger.exception("Editor run %s: part %s could not be started", run.id, finished + 2)
            return FAILED, None, failure.message if isinstance(failure, DomainError) else PART_FAILED
        latest = await asyncio.to_thread(self.repository.find, run.id)
        if latest is None or latest.terminal or run.id in self._cancelled:
            self._live.pop(generation_id, None)
            await self._abort_generation(generation_id, job.user)
            return CANCELLED, None, None
        return None

    async def _finish_part(self, run: EditorRun, index: int) -> None:
        latest = await asyncio.to_thread(self.repository.find, run.id)
        crop = dict(latest.crop)
        state = dict(crop["parts"])
        state["done"] = index + 1
        crop["parts"] = state
        prefix = f"part{index}."
        entries = [
            (crop["files"].get(name), filename) for name, filename in crop["names"].items() if name.startswith(prefix)
        ]
        crop["files"] = {name: value for name, value in crop["files"].items() if not name.startswith(prefix)}
        crop["names"] = {name: value for name, value in crop["names"].items() if not name.startswith(prefix)}
        await asyncio.to_thread(self.repository.update, run.id, self.clock(), crop=crop)
        if entries:
            await self._discard(run.user_id, entries, run.id)

    async def _compose_parts(self, run: EditorRun) -> Tuple[str, Optional[List[Dict[str, Any]]], Optional[str]]:
        candidates: List[Dict[str, Any]] = []
        try:
            held: List[List[Any]] = []
            for item in run.crop["parts"]["items"]:
                files = self.generations.get_files(item["generation_id"], is_final=True, file_type="IMAGE")
                if not files:
                    files = self.generations.get_files(item["generation_id"], file_type="IMAGE")
                if not files:
                    return FAILED, None, NO_PICTURE
                held.append(list(files)[:MAX_CANDIDATES])
            count = min(len(files) for files in held)
            rendered = await asyncio.to_thread(self._render_parts, run, held, count)
            output = (run.crop or {}).get("output")
            for data, file_ids, seed in rendered:
                stored = await self._put(run.user_id, "editor-patch.png", data)
                candidate = {
                    "patch": {"item_id": stored.id},
                    "url": stored.url,
                    "seed": seed,
                    "generation_file_id": file_ids[0],
                    "generation_file_ids": file_ids,
                }
                if output:
                    candidate["size"] = {"width": output[0], "height": output[1]}
                candidates.append(candidate)
            return DONE, candidates, None
        except Exception:
            logger.exception("Editor run %s: stitching failed", run.id)
            return FAILED, None, NOT_PLACED

    def _render_parts(
        self, run: EditorRun, held: List[List[Any]], count: int
    ) -> List[Tuple[bytes, List[str], Optional[int]]]:
        crop = run.crop
        items = crop["parts"]["items"]
        seeds = self._seeds(items[0]["generation_id"])
        base = self._load(run.user_id, crop["files"]["base"], "RGB")
        rendered = []
        if crop["compose"] == COMPOSE_TILES:
            tiles = [upscale.Tile.from_dict(item["tile"]) for item in items]
            for candidate in range(count):
                canvas = upscale.Canvas(tuple(crop["output"]))
                for tile, files in zip(tiles, held):
                    canvas.place(tile, self._result_image(run.user_id, files[candidate]))
                data = png_bytes(Image.fromarray(canvas.rgba()))
                rendered.append((data, [files[candidate].id for files in held], seeds.get(candidate)))
            return rendered
        region = self._load(run.user_id, crop["files"]["region"], "L")
        found = parts.Components(region)
        geometries = [Geometry.from_dict(item["geometry"]) for item in items]
        cuts = []
        for index, geometry in enumerate(geometries):
            top, bottom, left, right = geometry.box
            cuts.append((base[top:bottom, left:right], found.mask(index)[top:bottom, left:right]))
        union = parts.union_box([geometry.box for geometry in geometries])
        for candidate in range(count):
            placed = []
            for geometry, (base_cut, mask_cut), files in zip(geometries, cuts, held):
                result = self._result_image(run.user_id, files[candidate])
                placed.append((geometry.box, regions.stitch(base_cut, mask_cut, result, geometry)))
            data = png_bytes(Image.fromarray(parts.compose_patches(placed, union)))
            rendered.append((data, [files[candidate].id for files in held], seeds.get(candidate)))
        return rendered

    def _result_image(self, user_id: str, record: Any) -> Image.Image:
        with self.media_tools.open_input(user_id, {"generation_file_id": record.id}) as path:
            with Image.open(path) as opened:
                return opened.convert("RGB")

    async def _announce(self, run: Optional[EditorRun]) -> None:
        if run is None or not run.generation_id:
            return
        message = {"type": MESSAGE_TYPE, "generation_id": run.generation_id, "run": run.to_dict()}
        try:
            await self.hub.broadcast_to_generation(run.generation_id, message)
        except Exception:
            logger.exception("Editor run %s: the update could not be sent", run.id)

    async def _finish(self, run_id: str, hint: Optional[str] = None, generation_id: Optional[str] = None) -> None:
        if run_id in self._finishing:
            self._rerun[run_id] = (hint, generation_id)
            return
        self._finishing.add(run_id)
        try:
            while True:
                chained = False
                try:
                    chained = bool(await self._settle_from_generation(run_id, hint, generation_id))
                except Exception:
                    logger.exception("Editor run %s could not be finished", run_id)
                    await self._fail(run_id, NOT_FINISHED)
                    break
                if run_id in self._rerun:
                    hint, generation_id = self._rerun.pop(run_id)
                elif chained:
                    hint = generation_id = None
                else:
                    break
        finally:
            self._finishing.discard(run_id)
            self._rerun.pop(run_id, None)

    async def _settle_from_generation(
        self, run_id: str, hint: Optional[str], hinted: Optional[str] = None
    ) -> bool:
        run = await asyncio.to_thread(self.repository.find, run_id)
        if run is None or run.terminal or not run.generation_id:
            return False
        if (run.crop or {}).get("parts") is not None:
            await self._settle_parts(run, hint, hinted)
            return False
        generation = await asyncio.to_thread(self.generations.get_by_id, run.generation_id)
        if generation is None:
            outcome = (FAILED, None, GENERATION_GONE)
        else:
            status = getattr(generation, "status", None)
            if status not in TERMINAL_GENERATION:
                status = hint
            kind = (run.crop or {}).get("kind")
            if status == "completed" and kind == EXPAND_KIND:
                continued = await self._expand(run)
                if continued is None:
                    return True
                outcome = continued
            elif status == "completed":
                outcome = await (self._clips(run) if kind == RANGE_KIND else self._patches(run))
            elif status == "failed":
                outcome = (FAILED, None, getattr(generation, "error_user_message", None) or GENERATION_FAILED)
            elif status == "cancelled":
                outcome = (CANCELLED, None, None)
            else:
                return False
        settled = await asyncio.to_thread(self.repository.settle, run.id, self.clock(), *outcome)
        await self._after_settle(await asyncio.to_thread(self.repository.find, run.id), settled)
        return False

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

    async def _clips(self, run: EditorRun) -> Tuple[str, Optional[List[Dict[str, Any]]], Optional[str]]:
        candidates: List[Dict[str, Any]] = []
        try:
            files = self.generations.get_files(run.generation_id, is_final=True, file_type="VIDEO")
            if not files:
                files = self.generations.get_files(run.generation_id, file_type="VIDEO")
            if not files:
                return FAILED, None, NO_VIDEO
            rendered = await asyncio.to_thread(self._render_clips, run, files)
            for data, file_id, seed, detail in rendered:
                stored = await self._put(run.user_id, "editor-clip.mp4", data, False, CLIP_TYPE)
                candidates.append(
                    {"clip": {"item_id": stored.id}, "url": stored.url, "seed": seed, "generation_file_id": file_id, **detail}
                )
            return DONE, candidates, None
        except Exception:
            logger.exception("Editor run %s: splicing failed", run.id)
            return FAILED, None, NOT_SPLICED

    def _render_clips(self, run: EditorRun, files: List[Any]) -> List[Tuple[bytes, str, Optional[int], Dict[str, Any]]]:
        crop = run.crop or {}
        task = self.tasks.registry.get(run.task)
        facts = clips.ClipFacts(
            width=crop["width"], height=crop["height"], fps=crop["fps"], frames=crop["frames"],
            duration=crop["duration"], has_audio=crop["has_audio"],
        )
        window = clips.FrameRange(crop["start_frame"], crop["end_frame"], crop["fps"])
        base_id = (crop.get("files") or {})["base"]
        seeds = self._seeds(run.generation_id)
        rendered = []
        with tempfile.TemporaryDirectory() as scratch, self.media_tools.open_input(run.user_id, {"item_id": base_id}) as base:
            folder = Path(scratch)
            for index, record in enumerate(files[:MAX_CANDIDATES]):
                with self.media_tools.open_input(run.user_id, {"generation_file_id": record.id}) as result:
                    out = folder / f"out{index}.mp4"
                    if task is not None and task.result == "append":
                        sound = task.audio == "result" and clips.facts_of(result).has_audio
                        added = clips.conform(
                            result, facts, folder / f"added{index}.mp4", skip=window.count, with_audio=sound
                        )
                        frames = clips.facts_of(added).frames
                        clips.splice_append(base, facts, window.end, added, frames, sound, out)
                        detail = {
                            "added": {"at_s": round(window.end_s, 6), "seconds": round(frames / facts.fps, 6)},
                            "duration_s": round(facts.duration + frames / facts.fps, 6),
                        }
                    else:
                        patch = clips.conform(
                            result, facts, folder / f"patch{index}.mp4", count=window.count,
                            adopt_size=bool(crop.get("adopt_size")),
                        )
                        clips.splice_replace(base, facts, window, patch, out)
                        detail = {
                            "range": {"start_s": round(window.start_s, 6), "end_s": round(window.end_s, 6)},
                            "duration_s": round(facts.duration, 6),
                        }
                    rendered.append((out.read_bytes(), record.id, seeds.get(index), detail))
        return rendered

    def _render(self, run: EditorRun) -> List[Tuple[bytes, str, Optional[int]]]:
        files = self.generations.get_files(run.generation_id, is_final=True, file_type="IMAGE")
        if not files:
            files = self.generations.get_files(run.generation_id, file_type="IMAGE")
        if not files:
            return []
        geometry = Geometry.from_dict(run.crop)
        held = run.crop.get("files") or {}
        task = self.tasks.registry.get(run.task)
        if task is not None and task.produces == "matte":
            return self._render_cutouts(run, files, geometry)
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

    def _render_cutouts(self, run: EditorRun, files: List[Any], geometry: Geometry) -> List[Tuple[bytes, str, Optional[int]]]:
        seeds = self._seeds(run.generation_id)
        rendered = []
        for index, record in enumerate(files[:MAX_CANDIDATES]):
            with self.media_tools.open_input(run.user_id, {"generation_file_id": record.id}) as path:
                with Image.open(path) as opened:
                    result = opened.convert("RGBA")
            cutout = regions.cutout(result, geometry.picture)
            rendered.append((png_bytes(Image.fromarray(cutout)), record.id, seeds.get(index)))
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
