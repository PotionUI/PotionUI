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
`/models/{folder}` listings are cached per-process for a short TTL: a preset
can carry many `comfyui_node`/`comfyui_model` entries and an evaluation run
should not refetch the same listing once per entry.

`/object_info` is used here (and only here) to answer "is this node class
installed" - `ComfyUIBackend.list_models()` deliberately never uses it for
model listings (two incompatible schema shapes, phantom entries; see
docs/models.md), which is why model presence goes through `GET
/models/{folder}` instead, exactly like that method does.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import aiohttp
from pydantic import BaseModel, ConfigDict

from src.plugin_api.presets import (
    RequirementAction,
    RequirementContext,
    RequirementResult,
)

_REQUEST_TIMEOUT_SECONDS = 5.0
_CACHE_TTL_SECONDS = 60.0


class _TTLCache:
    """A tiny per-process, per-key TTL cache. Shared module-level instances
    below back both checkers so a preset with many entries evaluated in one
    run does not refetch the same `/object_info` or `/models/{folder}`
    listing once per entry."""

    def __init__(self, ttl_seconds: float):
        self._ttl = ttl_seconds
        self._entries: Dict[Any, Tuple[float, Any]] = {}

    def get(self, key: Any) -> Optional[Any]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._entries[key]
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        self._entries[key] = (time.monotonic() + self._ttl, value)


_object_info_cache = _TTLCache(_CACHE_TTL_SECONDS)
_model_list_cache = _TTLCache(_CACHE_TTL_SECONDS)


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


async def _fetch_object_info(backend_id: str, base_url: str) -> Dict[str, Any]:
    cached = _object_info_cache.get(backend_id)
    if cached is not None:
        return cached
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{base_url}/object_info") as resp:
            resp.raise_for_status()
            data = await resp.json()
    _object_info_cache.set(backend_id, data)
    return data


async def _fetch_model_names(backend_id: str, base_url: str, folder: str) -> List[str]:
    cache_key = (backend_id, folder)
    cached = _model_list_cache.get(cache_key)
    if cached is not None:
        return cached
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{base_url}/models/{folder}") as resp:
            resp.raise_for_status()
            names = await resp.json()
    _model_list_cache.set(cache_key, names)
    return names


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
