"""Preset requirement checkers for the `comfyui` engine: `comfyui_node`
(a custom node class is installed on the resolved backend's server) and
`comfyui_model` (a checkpoint/UNET/CLIP/VAE/LoRA file is present in one of
its model folders). Registered via `manifest.yml` `requirement_checkers:` -
see `src.plugin_api.presets.RequirementChecker` and docs/presets.md
"Requirements".

Both checkers read `ctx.backend` (the preset's resolved backend, injected by
`src.features.presets.requirements.context_builder`) rather than reaching
into a container - core's checker contract passes everything by value so a
plugin checker only ever needs `src.plugin_api.presets`. `/object_info` and
`/models/{folder}` listings are cached per-process for a short TTL, single-
flight: `evaluate_preset_requirements` runs every `requirements:` entry
concurrently, so a preset with many `comfyui_node`/`comfyui_model` entries
would otherwise fire that many parallel fetches of the exact same listing -
on a large custom-node install `/object_info` can run to several MB, so N
parallel fetches each competing for bandwidth/CPU-to-parse is exactly what
was blowing every entry's timeout in practice. The first caller for a key
fetches; every concurrent caller for that same key awaits that one fetch
instead of starting its own. Both checkers also declare a longer `timeout_s`
(see `src.plugin_api.presets.RequirementChecker`) since even a single
`/object_info` fetch/parse can take several seconds on a big install.

`/object_info` is used here (and only here) to answer "is this node class
installed" - `ComfyUIBackend.list_models()` deliberately never uses it for
model listings (two incompatible schema shapes, phantom entries; see
docs/models.md), which is why model presence goes through `GET
/models/{folder}` instead, exactly like that method does.
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

import aiohttp
from pydantic import BaseModel, ConfigDict

from src.plugin_api.presets import (
    RequirementAction,
    RequirementContext,
    RequirementResult,
)

_REQUEST_TIMEOUT_SECONDS = 5.0
_CACHE_TTL_SECONDS = 60.0
# `/object_info` on a big custom-node install can take several seconds to
# fetch and parse even when the server is perfectly healthy - longer than the
# evaluator's generic 5s default (src.features.presets.requirements.evaluator
# .CHECK_TIMEOUT_SECONDS), which would otherwise read every check as
# "unknown" on exactly the installs this feature matters most for.
_CHECK_TIMEOUT_SECONDS = 20.0


class _SingleFlightTTLCache:
    """A per-key TTL cache where concurrent callers for the same key that
    misses share one in-flight fetch, rather than each starting their own -
    `evaluate_preset_requirements` runs every `requirements:` entry
    concurrently, so without this an N-entry preset would fire N parallel
    fetches of the identical listing. Shared module-level instances below
    back both checkers."""

    def __init__(self, ttl_seconds: float):
        self._ttl = ttl_seconds
        self._ready: Dict[Any, Tuple[float, Any]] = {}
        self._inflight: Dict[Any, "asyncio.Future[Any]"] = {}
        self._lock = asyncio.Lock()

    async def get_or_fetch(self, key: Any, fetch: Callable[[], Awaitable[Any]]) -> Any:
        cached = self._get_ready(key)
        if cached is not None:
            return cached

        async with self._lock:
            # Another caller may have finished (or started) the fetch for
            # this key while we were waiting for the lock above.
            cached = self._get_ready(key)
            if cached is not None:
                return cached
            future = self._inflight.get(key)
            if future is None:
                future = asyncio.ensure_future(fetch())
                self._inflight[key] = future

        try:
            value = await future
        finally:
            async with self._lock:
                # Only the caller that actually owns this future clears it -
                # a late arrival that reused it must not clear a newer one.
                if self._inflight.get(key) is future:
                    del self._inflight[key]

        self._ready[key] = (time.monotonic() + self._ttl, value)
        return value

    def _get_ready(self, key: Any) -> Optional[Any]:
        entry = self._ready.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._ready[key]
            return None
        return value


_object_info_cache = _SingleFlightTTLCache(_CACHE_TTL_SECONDS)
_model_list_cache = _SingleFlightTTLCache(_CACHE_TTL_SECONDS)


class ComfyUINodeRequirementSchema(BaseModel):
    """`{type: comfyui_node, class_type: FaceDetailer}`."""

    model_config = ConfigDict(extra="forbid")

    type: str
    class_type: str
    hint: Optional[str] = None
    optional: bool = False


class ComfyUIModelRequirementSchema(BaseModel):
    """`{type: comfyui_model, folder: loras, name: my_style.safetensors}` -
    `folder` is a ComfyUI `models/` subfolder name (`checkpoints`,
    `diffusion_models`, `text_encoders`, `vae`, `loras`, ...)."""

    model_config = ConfigDict(extra="forbid")

    type: str
    folder: str
    name: str
    hint: Optional[str] = None
    optional: bool = False


def _resolve_comfyui_backend(ctx: RequirementContext) -> Union[Tuple[str, str], RequirementResult]:
    """The resolved backend's `(id, base_url)`, or the `RequirementResult` to
    return early when the preset has no usable `comfyui` backend."""
    backend = ctx.backend
    get_base_url = getattr(getattr(backend, "config", None), "get_base_url", None) if backend else None
    if backend is None or backend.engine != "comfyui" or get_base_url is None:
        return RequirementResult(
            status="unknown",
            detail="no ComfyUI backend configured",
            action=RequirementAction(kind="open_backends"),
        )
    return backend.id, get_base_url()


async def _get_object_info(base_url: str) -> Dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{base_url}/object_info") as resp:
            resp.raise_for_status()
            return await resp.json()


async def _fetch_object_info(backend_id: str, base_url: str) -> Dict[str, Any]:
    return await _object_info_cache.get_or_fetch(backend_id, lambda: _get_object_info(base_url))


async def _get_model_names(base_url: str, folder: str) -> List[str]:
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{base_url}/models/{folder}") as resp:
            resp.raise_for_status()
            return await resp.json()


async def _fetch_model_names(backend_id: str, base_url: str, folder: str) -> List[str]:
    return await _model_list_cache.get_or_fetch(
        (backend_id, folder), lambda: _get_model_names(base_url, folder)
    )


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _model_present(name: str, names: List[str]) -> bool:
    if name in names:
        return True
    wanted = _basename(name)
    return any(_basename(candidate) == wanted for candidate in names)


class ComfyUINodeChecker:
    type = "comfyui_node"
    schema = ComfyUINodeRequirementSchema
    timeout_s = _CHECK_TIMEOUT_SECONDS
    # This checker's answer depends on which ComfyUI server is asked - see
    # `RequirementChecker`'s docstring - so it is evaluated once per enabled
    # `comfyui` backend, not once for the whole preset.
    scope = "backend"

    async def check(self, spec: Dict[str, Any], ctx: RequirementContext) -> RequirementResult:
        parsed = self.schema.model_validate(spec)
        resolved = _resolve_comfyui_backend(ctx)
        if isinstance(resolved, RequirementResult):
            return resolved
        backend_id, base_url = resolved

        try:
            object_info = await _fetch_object_info(backend_id, base_url)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return RequirementResult(status="unknown", detail=f"backend unreachable at {base_url}: {e}")

        if parsed.class_type in object_info:
            return RequirementResult(status="ok", detail=f"node '{parsed.class_type}' is installed")

        return RequirementResult(
            status="missing",
            detail=f"node '{parsed.class_type}' is not installed on this ComfyUI server",
            hint=parsed.hint or f"Install the node pack that provides {parsed.class_type} (ComfyUI Manager), then re-check",
            action=RequirementAction(kind="open_backends"),
        )

    def describe(self, spec: Dict[str, Any]) -> str:
        return spec.get("class_type", "comfyui_node")


class ComfyUIModelChecker:
    type = "comfyui_model"
    schema = ComfyUIModelRequirementSchema
    timeout_s = _CHECK_TIMEOUT_SECONDS
    # Same reasoning as `ComfyUINodeChecker.scope`.
    scope = "backend"

    async def check(self, spec: Dict[str, Any], ctx: RequirementContext) -> RequirementResult:
        parsed = self.schema.model_validate(spec)
        resolved = _resolve_comfyui_backend(ctx)
        if isinstance(resolved, RequirementResult):
            return resolved
        backend_id, base_url = resolved

        try:
            names = await _fetch_model_names(backend_id, base_url, parsed.folder)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            return RequirementResult(status="unknown", detail=f"backend unreachable at {base_url}: {e}")

        if _model_present(parsed.name, names):
            return RequirementResult(status="ok", detail=f"'{parsed.name}' present in {parsed.folder}")

        return RequirementResult(
            status="missing",
            detail=f"'{parsed.name}' not found in this ComfyUI server's {parsed.folder} folder",
            hint=parsed.hint or f"Put {parsed.name} in ComfyUI's models/{parsed.folder}",
            action=RequirementAction(kind="open_downloader", payload={"query": parsed.name}),
        )

    def describe(self, spec: Dict[str, Any]) -> str:
        return spec.get("name", "comfyui_model")
