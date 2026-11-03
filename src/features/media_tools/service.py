import asyncio
import io
import logging
import math
import os
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple, Union, TYPE_CHECKING

import numpy as np
from PIL import Image, ImageOps
from pydantic import ValidationError

from src.features.media_tools.dto import (
    TERMINAL_STATUSES,
    ArtifactDTO,
    ClipPreviewResponse,
    EffectAnalysisResponse,
    InputRef,
    OutputSpec,
    PanelsRequest,
    RunDTO,
    RunError,
    RunResult,
    RunSummaryDTO,
    StepRequest,
    ToolDTO,
)
from src.features.media_tools.jobs import (
    SHUTDOWN_WAIT_SECONDS,
    MediaToolJobManager,
    RunControl,
    RunOutcome,
)
from src.features.media.validators import UPLOAD_PURPOSE_DERIVED
from src.features.media_tools.previews import FramePreview, PreviewCache, digest, is_preview_id
from src.features.media_tools.publisher import MediaToolPublisher, to_item
from src.features.media_tools.repository import MediaToolParamsRepository
from src.features.media_tools.sources import InputResolver, ResolvedInput
from src.platform.http.errors import DomainError, InvalidInputError, NotFoundError
from src.platform.media_tools import (
    InvalidSelection,
    InvalidToolParams,
    MediaToolError,
    MediaToolRegistry,
    ToolCancelled,
    ToolContext,
    ToolFailed,
    ToolSpec,
    UnsupportedMedia,
    run_chain,
    validate_params,
)
from src.platform.media_tools.chain import Step
from src.platform.media_tools.effects.assets import AssetError, load_lut
from src.platform.media_tools.effects.spec import EffectSpec
from src.platform.media_tools.params import LUT_PARAM, coerce_value as coerce_param
from src.platform.media_tools.effects.tool import ANALYSIS_KEY, EffectTool
from src.platform.media_tools.ops.slice import MAX_SOURCE_PIXELS, cell_names, parse_cells
from src.platform.media_tools.panels import ANALYSIS_SIDE, MAX_GRID_SIDE, detect
from src.platform.media_tools.fastpath import CRF_BY_QUALITY
from src.platform.media_tools.params import bind_media, media_params
from src.platform.media_tools.probe import has_alpha_mode
from src.platform.media_tools.still import excerpt_steps, read_frame, render_still
from src.platform.media_tools.timeline import Clock, StepContext
from src.platform.media_tools.types import MEDIA_KINDS, ToolMedia

if TYPE_CHECKING:
    from src.features.collections.repository import CollectionRepository
    from src.features.generation.file_repository import FileRepository
    from src.features.media.media_types import MediaTypeResolver
    from src.features.media.store import MediaStore
    from src.features.media.upload_repository import UploadRepository
    from src.features.plans.guard import LimitGuard
    from src.platform.filesystem.storage_driver import FileStorageDriver
    from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)

MAX_STEPS = 64
MAX_INPUTS = 50
MAX_OUTPUTS = 500
PREVIEW_CONCURRENCY = 2
PREVIEW_WAIT_SECONDS = 5.0
PREVIEW_TIME_TOLERANCE = 0.05
PREVIEW_END_BACKOFF_SECONDS = 0.5
PIXEL_UNIT = "px"
SLICE_TOOL = "slice"
LIBRARY_SCOPE = "library"
SCRATCH_PREFIX = "media-tool-"
STALE_SCRATCH_SECONDS = 6 * 3600.0
PREVIEW_SOURCE_SIDE = 1024
PREVIEW_OUTPUT_SIDE = 512
PREVIEW_QUALITY = 80
DEFAULT_SYNC_TIMEOUT_SECONDS = 600.0
SAVE_PROGRESS = 0.96
CHAIN_PROGRESS_SHARE = 0.95
FRAME_MEDIA_TYPES = {"webp": "image/webp", "png": "image/png"}
CLIP_SUFFIX_TYPES = {".mp4": "video/mp4", ".webm": "video/webm"}
PREVIEW_DIR_TAG = "previews-"
EFFECT_SHADER_URL = "/api/media-tools/effects/{tool_id}/shader"

StepLike = Union[StepRequest, Mapping[str, Any]]
InputLike = Union[InputRef, Mapping[str, Any]]
OutputLike = Union[OutputSpec, Mapping[str, Any], None]


@dataclass
class _Plan:
    steps: List[Step]
    inputs: List[ResolvedInput]
    mode: str
    attachments: dict
    target: Optional[ResolvedInput] = None
    collection_id: Optional[str] = None


def _ref_key(ref: Mapping[str, Any]) -> str:
    name = next(key for key in ("item_id", "generation_file_id") if ref.get(key))
    return f"{name}:{ref[name]}"


def _encode_frame(image: Image.Image, max_side: int, fmt: str) -> bytes:
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if has_alpha_mode(image) else "RGB")
    buffer = io.BytesIO()
    if fmt == "png":
        image.save(buffer, format="PNG")
    else:
        image.save(buffer, format="WEBP", quality=PREVIEW_QUALITY)
    return buffer.getvalue()


def _shared_generation_id(inputs: List[ResolvedInput]) -> Optional[str]:
    ids = {item.source_generation_id for item in inputs}
    return ids.pop() if len(ids) == 1 else None


def _user_id(user: Any) -> str:
    return str(getattr(user, "id", user))


def _safe_stem(name: Optional[str]) -> str:
    cleaned = (name or "").replace("\\", "-").replace("/", "-")
    return Path(cleaned).stem or "media"


def _load_preview_source(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        opened.thumbnail((PREVIEW_SOURCE_SIDE, PREVIEW_SOURCE_SIDE), Image.Resampling.LANCZOS)
        image = ImageOps.exif_transpose(opened) or opened
        image.load()
        return image


def _scale_pixel_params(spec: ToolSpec, params: Mapping[str, Any], side: int) -> dict:
    measured = [
        param
        for param in spec.params
        if param.get("unit") == PIXEL_UNIT
        and param.get("type") in ("integer", "float")
        and isinstance(params.get(param["name"]), (int, float))
        and not isinstance(params.get(param["name"]), bool)
    ]
    largest = max((abs(float(params[param["name"]])) for param in measured), default=0.0)
    scaled = dict(params)
    if largest <= side:
        return scaled
    factor = side / largest
    for param in measured:
        name = param["name"]
        value = float(params[name]) * factor
        if param.get("min") is not None:
            value = max(value, float(param["min"]))
        scaled[name] = int(round(value)) if param["type"] == "integer" else value
    return scaled


def _effect_of(spec: Optional[ToolSpec]) -> Optional[EffectSpec]:
    return spec.impl.effect if spec is not None and isinstance(spec.impl, EffectTool) else None


def effect_block(effect: EffectSpec) -> dict:
    etag = effect.shader_etag()
    return {
        "group": effect.group,
        "time": effect.time,
        "shader": {"url": EFFECT_SHADER_URL.format(tool_id=effect.tool_id), "etag": etag} if etag else None,
        "uniforms": effect.uniforms(),
        "textures": [
            {"uniform": texture.uniform, "from": texture.source, "kind": texture.kind} for texture in effect.textures
        ],
        "derive": [{"name": name, "expr": expression.source} for name, expression in effect.derive],
        "gain": effect.gain.source if effect.gain is not None else None,
        "analysis": effect.analysis is not None,
        "analysis_keys": list(effect.analysis.keys) if effect.analysis is not None else [],
    }


def _model(cls, value, field: str):
    if isinstance(value, cls):
        return value
    try:
        return cls.model_validate(value)
    except ValidationError as exc:
        raise InvalidInputError("invalid_params", f"Malformed {field}", field=field) from exc


def _refusal(exc: MediaToolError, **extra: Any) -> InvalidInputError:
    step_index = getattr(exc, "step_index", None)
    if isinstance(exc, InvalidToolParams):
        details = {"field": exc.field, **extra}
        if step_index is not None:
            details["step_index"] = step_index
        return InvalidInputError("invalid_params", exc.message, **details)
    if step_index is not None and "step_index" not in extra:
        extra = {**extra, "step_index": step_index}
    return InvalidInputError(exc.code, exc.message, **extra)


class MediaToolService:

    def __init__(
        self,
        registry: MediaToolRegistry,
        params_repository: MediaToolParamsRepository,
        upload_repository: "UploadRepository",
        file_repository: "FileRepository",
        media_type_resolver: "MediaTypeResolver",
        storage_driver: "FileStorageDriver",
        limit_guard: Optional["LimitGuard"] = None,
        jobs: Optional[MediaToolJobManager] = None,
        scratch_root: Optional[Path] = None,
        settings: Optional["Settings"] = None,
        thumbnail_runner: Optional[Callable[[Callable[[], None]], None]] = None,
        sweep_stale: bool = True,
        preview_wait_seconds: float = PREVIEW_WAIT_SECONDS,
        artifact_store: Optional["MediaStore"] = None,
        collection_repository: Optional["CollectionRepository"] = None,
    ):
        self.registry = registry
        self.collection_repo = collection_repository
        self.upload_repo = upload_repository
        self.artifact_store = artifact_store
        self.params_repo = params_repository
        self.resolver = InputResolver(upload_repository, file_repository, storage_driver)
        self.publisher = MediaToolPublisher(
            upload_repository,
            media_type_resolver,
            storage_driver,
            limit_guard,
            settings=settings,
            thumbnail_runner=thumbnail_runner,
        )
        self.jobs = jobs if jobs is not None else MediaToolJobManager()
        self.scratch_root = Path(scratch_root) if scratch_root else Path(tempfile.gettempdir())
        self._scratch_lock = threading.Lock()
        self._live_scratch: set = set()
        self._preview_slots = threading.BoundedSemaphore(PREVIEW_CONCURRENCY)
        self._preview_wait = preview_wait_seconds
        self.previews = PreviewCache(self._preview_dir)
        if sweep_stale:
            self.sweep_scratch()

    @contextmanager
    def _scratch(self):
        path = self.scratch_root / f"{SCRATCH_PREFIX}{uuid.uuid4().hex}"
        path.mkdir(parents=True)
        with self._scratch_lock:
            self._live_scratch.add(path)
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)
            with self._scratch_lock:
                self._live_scratch.discard(path)

    def sweep_scratch(self, max_age: float = STALE_SCRATCH_SECONDS, now: Optional[float] = None) -> int:
        try:
            entries = list(os.scandir(self.scratch_root))
        except OSError:
            return 0
        with self._scratch_lock:
            live = {str(path) for path in self._live_scratch}
        cutoff = (time.time() if now is None else now) - max_age
        removed = 0
        for entry in entries:
            if not entry.name.startswith(SCRATCH_PREFIX) or entry.path in live:
                continue
            try:
                if not entry.is_dir(follow_symlinks=False) or entry.stat(follow_symlinks=False).st_mtime > cutoff:
                    continue
            except OSError:
                continue
            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 1
        return removed

    def _preview_dir(self) -> Path:
        path = self.scratch_root / f"{SCRATCH_PREFIX}{PREVIEW_DIR_TAG}{uuid.uuid4().hex}"
        path.mkdir(parents=True)
        with self._scratch_lock:
            self._live_scratch.add(path)
        return path

    def shutdown(self, timeout: float = SHUTDOWN_WAIT_SECONDS) -> None:
        self.jobs.shutdown(timeout)
        self.previews.clear()
        with self._scratch_lock:
            leftover = list(self._live_scratch)
        for path in leftover:
            shutil.rmtree(path, ignore_errors=True)

    def catalog(
        self, user: Any, kind: Optional[str] = None, scope: Optional[str] = None
    ) -> List[ToolDTO]:
        specs = self.registry.list(kind=kind or None, scope=scope or None)
        remembered = self.params_repo.get_many(_user_id(user), [spec.id for spec in specs])
        tools = []
        for spec in specs:
            effect = _effect_of(spec)
            served = spec.to_dict()
            if effect is not None:
                served["params"] = self._with_lut_options(served["params"], spec.impl.runtime)
            tools.append(ToolDTO(
                **served,
                runnable=spec.impl is not None,
                last_params=remembered.get(spec.id),
                effect=effect_block(effect) if effect is not None else None,
            ))
        return tools

    @staticmethod
    def _with_lut_options(params: List[dict], runtime: Any) -> List[dict]:
        if not any(param.get("type") == LUT_PARAM for param in params):
            return params
        files = runtime.list_assets(LUT_PARAM, ".cube")
        options = [{"label": "None", "value": ""}] + [
            {"label": Path(ref).stem.replace("_", " "), "value": ref} for ref in files
        ]
        return [{**param, "options": options} if param.get("type") == LUT_PARAM else param for param in params]

    def _effect_runtime(self) -> Any:
        return next((spec.impl.runtime for spec in self.registry.list() if isinstance(spec.impl, EffectTool)), None)

    def lut_table(self, ref: str) -> bytes:
        try:
            ref = coerce_param({"name": "lut", "label": "LUT", "type": LUT_PARAM}, ref)
        except MediaToolError:
            raise NotFoundError("not_found", "LUT not found") from None
        runtime = self._effect_runtime()
        path = runtime.resolve_asset(LUT_PARAM, ref) if runtime is not None and ref else None
        if path is None:
            raise NotFoundError("not_found", "LUT not found")
        try:
            cube = load_lut(path)
        except (AssetError, OSError) as exc:
            raise InvalidInputError("invalid_lut", str(exc)) from None
        return np.round(np.clip(cube.data, 0.0, 1.0) * 65535).astype("<u2").tobytes()

    def effect_shader(self, tool_id: str) -> Tuple[str, str]:
        effect = _effect_of(self.registry.get(tool_id))
        if effect is None or effect.shader is None:
            raise NotFoundError("not_found", "Effect shader not found")
        return effect.shader, effect.shader_etag()

    def effect_analysis(
        self, user: Any, input_ref: InputLike, steps: Sequence[StepLike], step_index: int
    ) -> EffectAnalysisResponse:
        user_id = _user_id(user)
        models = [_model(StepRequest, step, "steps") for step in steps or []]
        if not 0 <= step_index < len(models) or models[step_index].hidden:
            raise InvalidInputError("invalid_params", "step_index must name a visible step", field="step_index")
        chain = self._editor_steps(models[: step_index + 1])
        target, params = chain[-1]
        effect = _effect_of(target)
        if effect is None or effect.analysis is None:
            raise InvalidInputError("invalid_params", f"{target.label} has nothing to measure", field="step_index")
        resolved = self.resolver.resolve(user_id, _model(InputRef, input_ref, "input"))
        if resolved.kind != "video":
            raise InvalidInputError("unsupported_media", "Only a video can be measured")
        attachments = self._resolve_attachments(user_id, chain)
        memo = self.previews.memo(resolved.key)
        computed = []

        def remember(key: tuple, compute: Callable[[], Any]) -> Any:
            def measure() -> Any:
                computed.append(key)
                return compute()

            return memo(key, measure)

        with self._preview_slot(), self._scratch() as scratch, ExitStack() as stack:
            ctx = ToolContext(scratch)
            try:
                path = stack.enter_context(self.resolver.stage(resolved))
                media = ctx.probe(path, "video")
                bound = self._bind(chain, attachments, stack, ctx)
                clock = Clock(length=media.duration)
                for spec, step_params in bound:
                    clock = spec.impl.retime(clock, step_params)
                prepared = target.impl.prepare(
                    bound[-1][1], StepContext(source=media, clock=clock, ctx=ctx, memo=remember)
                )
            except (InvalidToolParams, UnsupportedMedia, InvalidSelection) as exc:
                raise _refusal(exc) from exc
        return EffectAnalysisResponse(values=prepared[ANALYSIS_KEY], cached=not computed)

    def last_params(self, user: Any, tool_id: str) -> Optional[dict]:
        return self.params_repo.get(_user_id(user), tool_id)

    def check(
        self,
        user: Any,
        steps: Sequence[StepLike],
        inputs: Sequence[InputLike],
        output: OutputLike = None,
    ) -> List[ResolvedInput]:
        return self._plan(_user_id(user), steps, inputs, output).inputs

    def start_run(
        self,
        user: Any,
        steps: Sequence[StepLike],
        inputs: Sequence[InputLike],
        output: OutputLike = None,
    ) -> str:
        user_id = _user_id(user)
        plan = self._plan(user_id, steps, inputs, output)
        if plan.mode == "new":
            self.publisher.precheck(user_id)
        return self.jobs.submit(user_id, self._work(user_id, plan))

    def list_runs(self, user: Any) -> List[RunDTO]:
        return self.jobs.list(_user_id(user))

    def get_run(self, user: Any, run_id: str) -> RunDTO:
        run = self.jobs.get(_user_id(user), run_id)
        if run is None:
            raise NotFoundError("not_found", "Run not found")
        return run

    def cancel_run(self, user: Any, run_id: str) -> RunDTO:
        run = self.jobs.cancel(_user_id(user), run_id)
        if run is None:
            raise NotFoundError("not_found", "Run not found")
        return run

    def run_blocking(
        self,
        user: Any,
        steps: Sequence[StepLike],
        inputs: Sequence[InputLike],
        output: OutputLike = None,
        timeout: float = DEFAULT_SYNC_TIMEOUT_SECONDS,
    ) -> RunResult:
        user_id = _user_id(user)
        run_id = self.start_run(user_id, steps, inputs, output)
        run = self.jobs.wait(user_id, run_id, timeout)
        return self._settle(user_id, run_id, run)

    async def run_sync(
        self,
        user: Any,
        steps: Sequence[StepLike],
        inputs: Sequence[InputLike],
        output: OutputLike = None,
        timeout: float = DEFAULT_SYNC_TIMEOUT_SECONDS,
    ) -> RunResult:
        user_id = _user_id(user)
        starter = asyncio.ensure_future(
            asyncio.to_thread(self.start_run, user_id, steps, inputs, output)
        )
        try:
            run_id = await asyncio.shield(starter)
        except asyncio.CancelledError:
            started = (await asyncio.gather(starter, return_exceptions=True))[0]
            if isinstance(started, str):
                await asyncio.shield(asyncio.to_thread(self.jobs.cancel, user_id, started))
            raise
        loop = asyncio.get_running_loop()
        finished: asyncio.Future = loop.create_future()

        def resolve() -> None:
            if not finished.done():
                finished.set_result(None)

        def notify() -> None:
            try:
                loop.call_soon_threadsafe(resolve)
            except RuntimeError:
                return

        known = self.jobs.on_done(user_id, run_id, notify)
        try:
            if known:
                await asyncio.wait_for(finished, timeout)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self.jobs.cancel, user_id, run_id))
            raise
        run = await asyncio.to_thread(self.jobs.get, user_id, run_id)
        return await asyncio.to_thread(self._settle, user_id, run_id, run)

    def preview(
        self,
        user: Any,
        tool: str,
        params: Mapping[str, Any],
        input_ref: InputLike,
        time_seconds: Optional[float] = None,
    ) -> bytes:
        user_id = _user_id(user)
        spec = self._spec(tool)
        if not spec.preview or spec.impl is None or not spec.impl.has_image_path():
            raise InvalidInputError("unsupported_media", f"{spec.label} has no preview")
        at = self._preview_time(time_seconds)
        resolved = self.resolver.resolve(user_id, _model(InputRef, input_ref, "input"))
        if resolved.kind not in spec.accepts or resolved.kind == "audio":
            raise InvalidInputError(
                "unsupported_media", f"{spec.label} cannot be applied to {resolved.kind}"
            )
        try:
            validated = validate_params(spec, dict(params or {}))
        except InvalidToolParams as exc:
            raise _refusal(exc) from exc
        validated = _scale_pixel_params(spec, validated, PREVIEW_OUTPUT_SIDE)

        with self._preview_slot(), self._scratch() as scratch:
            try:
                with self.resolver.stage(resolved) as path:
                    source = self._preview_source(resolved, path, scratch, at)
                result = spec.impl.image(source, validated)
            except (InvalidToolParams, UnsupportedMedia, InvalidSelection) as exc:
                raise _refusal(exc) from exc
            return _encode_frame(result, PREVIEW_OUTPUT_SIDE, "webp")

    @contextmanager
    def _preview_slot(self):
        if not self._preview_slots.acquire(timeout=self._preview_wait):
            raise DomainError(
                "preview_busy", "Too many previews are rendering; try again in a moment", status_code=429
            )
        try:
            yield
        finally:
            self._preview_slots.release()

    def preview_frame(
        self,
        user: Any,
        input_ref: InputLike,
        steps: Sequence[StepLike],
        time_seconds: Optional[float] = None,
        max_side: int = 1280,
        fmt: str = "webp",
    ) -> FramePreview:
        user_id = _user_id(user)
        resolved = self.resolver.resolve(user_id, _model(InputRef, input_ref, "input"))
        if resolved.kind not in ("image", "video"):
            raise InvalidInputError("unsupported_media", f"A {resolved.kind} file has no frames to preview")
        at = self._preview_time(time_seconds)
        chain = self._editor_steps(steps)
        attachments = self._resolve_attachments(user_id, chain)
        media_type = FRAME_MEDIA_TYPES.get(fmt)
        if media_type is None:
            raise InvalidInputError("invalid_params", "format must be webp or png", field="format")
        key = digest(["frame", resolved.key, round(at, 3), int(max_side), fmt, self._chain_identity(chain, attachments)])
        cached = self.previews.frame(user_id, key)
        if cached is not None:
            return cached

        with self._preview_slot(), self._scratch() as scratch, ExitStack() as stack:
            ctx = ToolContext(scratch)
            try:
                path = stack.enter_context(self.resolver.stage(resolved))
                media = ctx.probe(path, resolved.kind)
                image, snapped = read_frame(media, at, ctx)
                bound = self._bind(chain, attachments, stack, ctx)
                result = render_still(bound, media, image, snapped, ctx, self.previews.memo(resolved.key))
            except (InvalidToolParams, UnsupportedMedia, InvalidSelection) as exc:
                raise _refusal(exc) from exc
            width, height = result.size
            data = _encode_frame(result, int(max_side), fmt)
        self.previews.store_frame(user_id, key, data, media_type, width, height)
        return FramePreview(data=data, media_type=media_type, cached=False, width=width, height=height)

    def start_clip_preview(
        self,
        user: Any,
        input_ref: InputLike,
        steps: Sequence[StepLike],
        start_seconds: float = 0.0,
        duration_seconds: float = 3.0,
        height: int = 480,
    ) -> ClipPreviewResponse:
        user_id = _user_id(user)
        resolved = self.resolver.resolve(user_id, _model(InputRef, input_ref, "input"))
        if resolved.kind != "video":
            raise InvalidInputError("unsupported_media", "Only a video has a clip preview")
        start = self._preview_time(start_seconds)
        chain = self._editor_steps(steps)
        attachments = self._resolve_attachments(user_id, chain)
        key = digest([
            "clip", resolved.key, round(start, 3), float(duration_seconds), int(height),
            self._chain_identity(chain, attachments),
        ])
        cached = self.previews.clip(user_id, key)
        if cached is not None:
            return ClipPreviewResponse(preview=cached)

        def work(control: RunControl) -> RunOutcome:
            with self._scratch() as scratch, ExitStack() as stack:
                ctx = ToolContext(scratch, progress=control.report, is_cancelled=control.is_cancelled)
                path = stack.enter_context(self.resolver.stage(resolved))
                media = ctx.probe(path, "video")
                bound = self._bind(chain, attachments, stack, ctx)
                plan = excerpt_steps(
                    bound, media, start, float(duration_seconds), int(height), CRF_BY_QUALITY["small"]
                )
                (produced,) = run_chain(plan, [media], ctx, validate=False)
                if control.is_cancelled():
                    raise ToolCancelled()
                clip = self.previews.store_clip(
                    user_id,
                    key,
                    produced.path,
                    CLIP_SUFFIX_TYPES.get(produced.path.suffix.lower(), "video/mp4"),
                    duration_seconds=produced.duration,
                    width=produced.width,
                    height=produced.height,
                )
                return RunOutcome(preview=clip)

        return ClipPreviewResponse(run_id=self.jobs.submit(user_id, work, kind="preview"))

    def preview_clip_file(self, user: Any, preview_id: str) -> Tuple[Path, str]:
        found = self.previews.clip_file(_user_id(user), preview_id) if is_preview_id(preview_id) else None
        if found is None:
            raise NotFoundError("not_found", "Preview not found")
        return found

    def check_artifact(self, user: Any, size: Optional[int], content_type: Optional[str]) -> None:
        store = self._artifacts()
        try:
            store.check_upload_size(_user_id(user), size, UPLOAD_PURPOSE_DERIVED, content_type or "")
        except ValueError as exc:
            raise InvalidInputError("upload_failed", str(exc) or "The file could not be stored") from exc

    def _artifacts(self) -> "MediaStore":
        if self.artifact_store is None:
            raise DomainError("artifacts_unavailable", "Uploads are not available here", status_code=503)
        return self.artifact_store

    async def upload_artifact(
        self, user: Any, data: bytes, filename: Optional[str], content_type: Optional[str]
    ) -> ArtifactDTO:
        store = self._artifacts()
        user_id = _user_id(user)
        name = _safe_stem(filename) + Path(filename or "").suffix.lower()
        try:
            stored = await store.upload_media(
                file_data=data,
                filename=name,
                content_type=content_type,
                user_id=user_id,
                purpose=UPLOAD_PURPOSE_DERIVED,
            )
        except ValueError as exc:
            raise InvalidInputError("upload_failed", str(exc) or "The file could not be stored") from exc
        row = await asyncio.to_thread(self.upload_repo.get_by_filename, stored.filename, user_id)
        if row is None:
            raise ToolFailed("The file could not be stored")
        return ArtifactDTO(
            id=row.id,
            media_type=row.media_type,
            mime_type=row.mime_type,
            url=f"/api/media/uploads/{row.filename}",
            width=row.width,
            height=row.height,
            duration_seconds=row.duration_seconds,
            fps=row.fps,
            size=row.file_size,
        )

    def detect_panels(self, user: Any, request: PanelsRequest) -> dict:
        options = _model(PanelsRequest, request, "input")
        resolved = self.resolver.resolve(_user_id(user), options.input)
        if resolved.kind != "image":
            raise InvalidInputError("unsupported_media", f"A {resolved.kind} file has no panels to detect")
        grid = self._grid(options.grid)
        with self._preview_slot(), self.resolver.stage(resolved) as path:
            image, size = self._panel_source(path)
            return detect(image, options.tolerance, grid, size)

    @staticmethod
    def _grid(grid: Optional[Sequence[int]]) -> Optional[Tuple[int, int]]:
        if grid is None:
            return None
        columns, rows = int(grid[0]), int(grid[1])
        if not (1 <= columns <= MAX_GRID_SIDE and 1 <= rows <= MAX_GRID_SIDE):
            raise InvalidInputError(
                "invalid_params", f"The grid needs 1 to {MAX_GRID_SIDE} columns and rows", field="grid"
            )
        return columns, rows

    @staticmethod
    def _panel_source(path: Path) -> Tuple[Image.Image, Tuple[int, int]]:
        try:
            with Image.open(path) as opened:
                if opened.width * opened.height > MAX_SOURCE_PIXELS:
                    raise InvalidInputError("invalid_params", "The picture is too large to analyse", field="input")
                image = ImageOps.exif_transpose(opened) or opened
                image.load()
                size = image.size
                image = image.convert("RGBA")
        except (Image.DecompressionBombError, Image.UnidentifiedImageError, OSError) as exc:
            raise InvalidInputError("unsupported_media", "The picture could not be read") from exc
        image.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE), Image.Resampling.BOX)
        return image, size

    def _check_slice_last(self, specs: List[ToolSpec]) -> None:
        for index, spec in enumerate(specs[:-1]):
            if spec.id == SLICE_TOOL:
                raise InvalidInputError(
                    "invalid_params", "Slice must be the last step", field="steps", step_index=index
                )

    def _editor_steps(self, steps: Sequence[StepLike]) -> List[Step]:
        models = self._visible(steps)
        specs = [self._spec(step.tool, index) for index, step in enumerate(models)]
        self._check_slice_last(specs)
        for index, spec in enumerate(specs):
            if spec.cardinality == "many_to_one":
                raise InvalidInputError(
                    "unsupported_media", f"{spec.label} has no preview", step_index=index
                )
        return list(zip(specs, self._validate_steps(specs, models)))

    def _visible(self, steps: Sequence[StepLike]) -> List[StepRequest]:
        models = [_model(StepRequest, step, "steps") for step in steps or []]
        visible = [step for step in models if not step.hidden]
        if len(visible) > MAX_STEPS:
            raise InvalidInputError(
                "invalid_params", f"A run needs between 1 and {MAX_STEPS} steps", field="steps"
            )
        return visible

    @staticmethod
    def _chain_identity(chain: List[Step], attachments: Mapping[str, ResolvedInput]) -> list:
        return [
            [[spec.id, spec.source, params] for spec, params in chain],
            sorted((name, item.key) for name, item in attachments.items()),
        ]

    def _resolve_attachments(self, user_id: str, chain: List[Step]) -> dict:
        found: dict = {}
        for index, (spec, params) in enumerate(chain):
            for param in media_params(spec):
                ref = params.get(param["name"])
                if not isinstance(ref, Mapping):
                    continue
                key = _ref_key(ref)
                item = found.get(key) or self.resolver.resolve(user_id, InputRef(**ref))
                accepts = tuple(param.get("accepts") or MEDIA_KINDS)
                if item.kind not in accepts:
                    raise InvalidInputError(
                        "unsupported_media",
                        f"{param.get('label') or param['name']} must be {' or '.join(accepts)}",
                        field=param["name"],
                        step_index=index,
                    )
                found[key] = item
        return found

    def _bind(
        self, chain: List[Step], attachments: Mapping[str, ResolvedInput], stack: ExitStack, ctx: ToolContext
    ) -> List[Step]:
        staged: dict = {}

        def lookup(param: dict, ref: dict) -> ToolMedia:
            key = _ref_key(ref)
            if key not in staged:
                item = attachments[key]
                path = stack.enter_context(self.resolver.stage(item))
                staged[key] = ctx.probe(path, item.kind)
            return staged[key]

        return [(spec, bind_media(spec, params, lookup)) for spec, params in chain]

    @staticmethod
    def _preview_time(time_seconds: Optional[float]) -> float:
        if time_seconds is None:
            return 0.0
        try:
            at = float(time_seconds)
        except (TypeError, ValueError):
            at = math.nan
        if not math.isfinite(at) or at < 0:
            raise InvalidInputError(
                "invalid_params", "time_seconds must be zero or more", field="time_seconds"
            )
        return at

    def _preview_source(
        self, resolved: ResolvedInput, path: Path, scratch: Path, at: float
    ) -> Image.Image:
        if resolved.kind == "image":
            return _load_preview_source(path)
        ctx = ToolContext(scratch)
        media = ctx.probe(path, "video")
        duration = media.duration
        if duration is not None and at > duration + PREVIEW_TIME_TOLERANCE:
            raise InvalidInputError(
                "invalid_params",
                f"time_seconds ({at:g}) is past the end of the video ({duration:.3f}s)",
                field="time_seconds",
            )
        frame = ctx.new_path(".png", stem="frame")
        for seek in self._frame_seeks(at, duration):
            ctx.ffmpeg(["-ss", f"{seek:.3f}", "-i", str(path), "-frames:v", "1", str(frame)])
            if frame.is_file() and frame.stat().st_size > 0:
                return _load_preview_source(frame)
        raise ToolFailed("Could not read a frame from the video")

    @staticmethod
    def _frame_seeks(at: float, duration: Optional[float]) -> List[float]:
        seeks = [at]
        if duration is not None and duration > 0:
            seeks.append(max(0.0, min(at, duration) - PREVIEW_END_BACKOFF_SECONDS))
            seeks.append(0.0)
        return seeks

    def _settle(self, user_id: str, run_id: str, run: Optional[RunDTO]) -> RunResult:
        if run is None:
            return RunResult(
                status="failed", run_id=run_id, error=RunError(code="not_found", message="Run not found")
            )
        if run.status not in TERMINAL_STATUSES:
            run = self.jobs.cancel(user_id, run_id) or run
            return RunResult(
                status="cancelled",
                run_id=run_id,
                error=RunError(code="timeout", message="The tool took too long and was cancelled"),
            )
        return RunResult(
            items=list(run.items),
            replaced=run.replaced,
            error=run.error,
            status=run.status,
            run_id=run_id,
            summary=run.summary,
        )

    def _spec(self, tool: str, index: Optional[int] = None) -> ToolSpec:
        spec = self.registry.get(tool)
        if spec is None or spec.impl is None:
            extra = {} if index is None else {"step_index": index}
            raise InvalidInputError("unknown_tool", f"Unknown tool: {tool}", **extra)
        return spec

    def _plan(
        self, user_id: str, steps: Sequence[StepLike], inputs: Sequence[InputLike], output: OutputLike
    ) -> _Plan:
        step_models = self._visible(steps)
        if not step_models:
            raise InvalidInputError(
                "invalid_params", f"A run needs between 1 and {MAX_STEPS} steps", field="steps"
            )
        specs = [self._spec(step.tool, index) for index, step in enumerate(step_models)]
        self._check_slice_last(specs)

        refs = [_model(InputRef, ref, "inputs") for ref in inputs or []]
        if not refs or len(refs) > MAX_INPUTS:
            raise InvalidInputError(
                "invalid_selection", f"Pick between 1 and {MAX_INPUTS} items to run a tool on"
            )
        resolved = [self.resolver.resolve(user_id, ref) for ref in refs]

        self._check_media(specs, resolved)
        validated = self._validate_steps(specs, step_models)

        spec_out = _model(OutputSpec, output if output is not None else {}, "output")
        mode = spec_out.mode
        chain = list(zip(specs, validated))
        target = None
        collection_id = self._check_collection(user_id, spec_out)
        if mode != "new" and any(spec.id == SLICE_TOOL for spec in specs):
            raise InvalidInputError("replace_not_allowed", "Slicing makes new items")
        if mode == "replace":
            self._check_replace(specs, resolved)
        elif mode == "update":
            target = self._check_update(user_id, specs, resolved, spec_out.target_item_id)
        attachments = self._resolve_attachments(user_id, chain)
        return _Plan(
            steps=chain,
            inputs=resolved,
            mode=mode,
            attachments=attachments,
            target=target,
            collection_id=collection_id,
        )

    def _check_collection(self, user_id: str, spec_out: OutputSpec) -> Optional[str]:
        if not spec_out.collection_id:
            return None
        if spec_out.mode != "new":
            raise InvalidInputError(
                "invalid_params", "A collection needs a run that makes new items", field="collection_id"
            )
        found = (
            self.collection_repo.get_by_id(spec_out.collection_id, LIBRARY_SCOPE, user_id)
            if self.collection_repo is not None
            else None
        )
        if found is None:
            raise NotFoundError("not_found", "Collection not found")
        return spec_out.collection_id

    def _check_media(self, specs: List[ToolSpec], resolved: List[ResolvedInput]) -> None:
        kinds = [item.kind for item in resolved]
        count_known = True
        for index, spec in enumerate(specs):
            for kind in kinds:
                if kind not in spec.accepts:
                    raise InvalidInputError(
                        "unsupported_media",
                        f"{spec.label} cannot be applied to {kind}",
                        step_index=index,
                    )
            if count_known:
                low, high = spec.selection_bounds()
                if len(kinds) < low or (high is not None and len(kinds) > high):
                    span = f"{low}" if high == low else (f"{low} to {high}" if high else f"at least {low}")
                    raise InvalidInputError(
                        "invalid_selection",
                        f"{spec.label} needs {span} item(s), got {len(kinds)}",
                        step_index=index,
                    )
            if spec.cardinality == "many_to_one":
                kinds = [spec.output_kind(kinds[0])]
            else:
                kinds = [spec.output_kind(kind) for kind in kinds]
                if spec.cardinality == "one_to_many":
                    count_known = False

    def _validate_steps(self, specs: List[ToolSpec], steps: List[StepRequest]) -> List[dict]:
        validated: List[dict] = []
        for index, (spec, step) in enumerate(zip(specs, steps)):
            try:
                validated.append(validate_params(spec, dict(step.params)))
            except InvalidToolParams as exc:
                raise _refusal(exc, step_index=index) from exc
        return validated

    def _check_replace(self, specs: List[ToolSpec], resolved: List[ResolvedInput]) -> None:
        kind = resolved[0].kind
        for spec in specs:
            kind = spec.output_kind(kind)
        allowed = (
            len(resolved) == 1
            and resolved[0].is_library_item
            and all(spec.cardinality == "one_to_one" for spec in specs)
            and kind == resolved[0].kind
        )
        if not allowed:
            raise InvalidInputError(
                "replace_not_allowed",
                "Replace works on a single library item and a tool that keeps its kind",
            )

    def _check_update(
        self, user_id: str, specs: List[ToolSpec], resolved: List[ResolvedInput], target_id: Optional[str]
    ) -> ResolvedInput:
        if not target_id:
            raise InvalidInputError(
                "invalid_params", "Save needs the Library copy to update", field="target_item_id"
            )
        target = self.resolver.resolve(user_id, InputRef(item_id=target_id))
        kind = resolved[0].kind
        for spec in specs:
            kind = spec.output_kind(kind)
        allowed = (
            len(resolved) == 1
            and target.is_library_item
            and all(spec.cardinality == "one_to_one" for spec in specs)
            and kind == target.kind
        )
        if not allowed:
            raise InvalidInputError(
                "update_not_allowed",
                "Save updates one Library copy with edits that keep its kind",
            )
        return target

    def _work(self, user_id: str, plan: _Plan) -> Callable[[RunControl], RunOutcome]:
        def work(control: RunControl) -> RunOutcome:
            with self._scratch() as scratch:
                base = ToolContext(
                    scratch, progress=control.report, is_cancelled=control.is_cancelled, user_id=user_id
                )
                ctx = base.scoped(0.0, CHAIN_PROGRESS_SHARE)
                with ExitStack() as stack:
                    paths = [stack.enter_context(self.resolver.stage(item)) for item in plan.inputs]
                    media = [ctx.probe(path, item.kind) for path, item in zip(paths, plan.inputs)]
                    bound = self._bind(plan.steps, plan.attachments, stack, ctx)
                    outputs = run_chain(bound, media, ctx, validate=False)
                    if control.is_cancelled():
                        raise ToolCancelled()
                    if len(outputs) > MAX_OUTPUTS:
                        raise InvalidInputError(
                            "too_many_outputs",
                            f"This run would create {len(outputs)} files; the limit is {MAX_OUTPUTS}",
                        )
                    if not outputs and base.summary is not None:
                        outcome = RunOutcome(summary=RunSummaryDTO.of(base.summary))
                    else:
                        control.report(SAVE_PROGRESS, "Saving")
                        outcome = self._publish(user_id, plan, outputs)
                self._remember(user_id, plan)
                return outcome

        return work

    def _publish(self, user_id: str, plan: _Plan, outputs: list) -> RunOutcome:
        if plan.mode in ("replace", "update"):
            if len(outputs) != 1:
                raise ToolFailed("The tool did not produce exactly one result")
            source = plan.target if plan.mode == "update" else plan.inputs[0]
            updated = self.publisher.publish_replace(user_id, source.upload, source.key, outputs[0])
            return RunOutcome(items=[to_item(updated)], replaced=True)
        rows = self.publisher.publish_new(
            user_id,
            outputs,
            self._names(plan, outputs),
            _shared_generation_id(plan.inputs),
            self._provenance(plan, outputs),
        )
        self._file_into_collection(user_id, plan, rows)
        return RunOutcome(items=[to_item(row) for row in rows], replaced=False)

    def _file_into_collection(self, user_id: str, plan: _Plan, rows: list) -> None:
        if not plan.collection_id or self.collection_repo is None:
            return
        try:
            self.collection_repo.add_upload_members(
                plan.collection_id, [row.id for row in rows], user_id, LIBRARY_SCOPE
            )
        except Exception:
            logger.exception("Could not file media tool outputs into collection %s", plan.collection_id)

    @staticmethod
    def _provenance(plan: _Plan, outputs: list) -> Optional[List[Optional[dict]]]:
        spec, params = plan.steps[-1]
        if spec.id != SLICE_TOOL or len(plan.inputs) != 1:
            return None
        source = plan.inputs[0]
        origin = (
            {"source_item_id": source.upload.id}
            if source.upload is not None
            else {"generation_file_id": source.generation_file_id}
        )
        cells = parse_cells(params["rects"])
        return [
            {
                "slice": {
                    **origin,
                    "source_name": source.original_filename,
                    "index": index,
                    "count": len(cells),
                    "rect": cell.rect(),
                }
            }
            for index, cell in enumerate(cells[: len(outputs)])
        ]

    def _names(self, plan: _Plan, outputs: list) -> List[Optional[str]]:
        last = plan.steps[-1][0]
        total = len(outputs)
        fanned = any(spec.cardinality == "one_to_many" for spec, _ in plan.steps)
        if last.id == SLICE_TOOL and len(plan.inputs) == 1:
            return self._slice_names(plan, outputs)
        names: List[Optional[str]] = []
        for index, media in enumerate(outputs):
            matched = last.cardinality == "one_to_one" and len(plan.inputs) == total
            source = plan.inputs[index] if matched else plan.inputs[0]
            stem = _safe_stem(source.original_filename)
            extension = media.path.suffix
            if last.cardinality == "one_to_many" or (fanned and total > len(plan.inputs)):
                names.append(f"{stem} — part {index + 1} of {total}{extension}")
            elif last.cardinality == "many_to_one":
                names.append(f"{stem}{last.name_suffix or '-joined'}{extension}")
            else:
                names.append(f"{stem}{last.name_suffix}{extension}")
        return names

    @staticmethod
    def _slice_names(plan: _Plan, outputs: list) -> List[Optional[str]]:
        spec, params = plan.steps[-1]
        stem = _safe_stem(plan.inputs[0].original_filename)
        typed = cell_names(params)
        used: set = set()
        names: List[Optional[str]] = []
        for index, media in enumerate(outputs):
            extension = media.path.suffix
            base = typed[index] if index < len(typed) and typed[index] else f"{stem}{spec.name_suffix}-{index + 1:02d}"
            if base.lower().endswith(extension.lower()):
                base = base[: -len(extension)] if extension else base
            candidate, number = f"{base}{extension}", 1
            while candidate.lower() in used:
                number += 1
                candidate = f"{base}-{number}{extension}"
            used.add(candidate.lower())
            names.append(candidate)
        return names

    def _remember(self, user_id: str, plan: _Plan) -> None:
        if len(plan.steps) != 1:
            return
        spec, params = plan.steps[0]
        if any(param.get("type") == "json" for param in spec.params):
            return
        try:
            self.params_repo.save(user_id, spec.id, params)
        except Exception:
            logger.exception("Could not remember the parameters of %s", spec.id)
