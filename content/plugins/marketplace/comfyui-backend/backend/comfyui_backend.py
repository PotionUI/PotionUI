"""
ComfyUI Backend Implementation

Provides the `comfyui` engine: presets whose pipelines contain a ComfyUIPipe.

Execution still happens in this process (see InProcessBackend) - what makes the
engine "ComfyUI" is that ComfyUIPipe talks to a ComfyUI server over HTTP/WS.
This backend's job is to inject that server's connection config into the pipes
before they run, so presets never hard-code a host or port.
"""

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List

import aiohttp

from src.plugin_api import (
    BackendModel,
    InProcessBackend,
    deduplicate,
)

logger = logging.getLogger(__name__)


# ComfyUI's `folder_paths` folder name -> PotionUI's model_type. This mapping is
# plugin-owned (core knows nothing about ComfyUI's on-disk layout). Only folders
# that appear in BOTH this map and the server's live `GET /models` response are
# enumerated - that intersection is the runtime discovery docs/models.md calls
# for, so a custom-node folder we don't recognise is silently skipped, and a
# folder we know about but the server doesn't have is skipped too.
#
# `clip` and `text_encoders` both map to `clip` and are merged into one listing.
FOLDER_TO_MODEL_TYPE = {
    "checkpoints": "checkpoint",
    "loras": "lora",
    "vae": "vae",
    "text_encoders": "clip",
    "clip": "clip",
    "diffusion_models": "diffusion_model",
    "unet": "unet",
    "upscale_models": "upscaler",
    "embeddings": "embedding",
    "controlnet": "controlnet",
    "clip_vision": "clip_vision",
}


class ComfyUIBackend(InProcessBackend):
    """A configured ComfyUI server."""

    def __init__(self, backend_config, generation_engine=None):
        super().__init__(backend_config, generation_engine)
        self.client_id = backend_config.client_id or str(uuid.uuid4())

    @property
    def host(self) -> str:
        return self.config.host

    @property
    def port(self) -> int:
        return self.config.port

    @property
    def base_url(self) -> str:
        """Get the base HTTP URL for the ComfyUI server"""
        return self.config.get_base_url()

    @property
    def ws_url(self) -> str:
        """Get the WebSocket URL for the ComfyUI server, scoped to our client id"""
        return f"{self.config.get_ws_url()}?clientId={self.client_id}"

    def get_connection_config(self) -> Dict[str, Any]:
        """Return connection config dict for injection into the pipeline"""
        config = self.config.to_connection_config()
        config["client_id"] = self.client_id
        return config

    def supports_model_listing(self) -> bool:
        return True

    async def list_models(self) -> List[BackendModel]:
        """Enumerate the models this ComfyUI server can load.

        Two-step discovery, per docs/models.md:

        1. `GET /models` -> the folder names the server actually has (its
           `folder_paths` registry - 65-odd on a typical install, including
           custom-node directories we don't recognise). We only enumerate the
           intersection of that set with `FOLDER_TO_MODEL_TYPE`.
        2. For each folder of interest, prefer `GET /experiment/models/{folder}`
           for name + size (`confidence=reported`); fall back to the stable
           `GET /models/{folder}` (names only, `confidence=name_only`) if the
           experimental endpoint 404s or errors.

        `/object_info` is deliberately never used - it has two incompatible
        schema shapes and reports phantom (non-file) entries. See docs/models.md.

        Raises on a server we can't reach at all; never returns [] silently for
        that case, since an empty list would be indistinguishable from "this
        server genuinely has no models".
        """
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(f"{self.base_url}/models") as resp:
                    resp.raise_for_status()
                    folders = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise ConnectionError(
                    f"Could not reach ComfyUI backend '{self.name}' at {self.base_url}: {e}"
                ) from e

            # Merge folders that map to the same model_type (clip + text_encoders).
            folders_by_type: Dict[str, List[str]] = {}
            for folder in folders:
                model_type = FOLDER_TO_MODEL_TYPE.get(folder)
                if model_type is None:
                    continue
                folders_by_type.setdefault(model_type, []).append(folder)

            entries: List[BackendModel] = []
            for model_type, folder_names in folders_by_type.items():
                for folder in folder_names:
                    entries.extend(await self._list_folder(session, folder, model_type))

        return deduplicate(entries)

    async def _list_folder(
        self, session: aiohttp.ClientSession, folder: str, model_type: str
    ) -> List[BackendModel]:
        """List one ComfyUI model folder, preferring the sized endpoint."""
        try:
            async with session.get(f"{self.base_url}/experiment/models/{folder}") as resp:
                resp.raise_for_status()
                items = await resp.json()
            return [
                BackendModel(
                    model_type=model_type,
                    filename=os.path.basename(item["name"]),
                    ref=item["name"],
                    size=item.get("size"),
                )
                for item in items
            ]
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.debug(
                f"[COMFYUI_BACKEND] /experiment/models/{folder} unavailable, "
                f"falling back to /models/{folder}"
            )

        async with session.get(f"{self.base_url}/models/{folder}") as resp:
            resp.raise_for_status()
            names = await resp.json()
        return [
            BackendModel(
                model_type=model_type,
                filename=os.path.basename(name),
                ref=name,
            )
            for name in names
        ]

    def prepare_pipes(self, pipes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Inject this server's connection config into every pipe's config dict.

        Pipes read it as ``self.config['backend_config']``; ComfyUIPipe uses it to
        reach the server. It is injected into every pipe rather than only the
        ComfyUI one so that plugin-provided pipes can reach the server too.
        """
        backend_config = self.get_connection_config()

        for pipe in pipes:
            if pipe.get('config') is None:
                pipe['config'] = {}
            pipe['config']['backend_config'] = backend_config

        logger.info(
            f"[COMFYUI_BACKEND] Injected connection config into {len(pipes)} pipes: "
            f"host={self.host}, port={self.port}"
        )
        return pipes

    async def cancel_generation(self, generation_id: str) -> bool:
        """Cancel a running generation, interrupting the ComfyUI server too."""
        if generation_id not in self._active:
            return False

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/interrupt",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        logger.info(f"[COMFYUI_BACKEND] Interrupted ComfyUI for {generation_id}")
        except Exception as e:
            logger.warning(f"[COMFYUI_BACKEND] Failed to interrupt ComfyUI: {e}")

        return await super().cancel_generation(generation_id)

    async def health_check(self) -> Dict[str, Any]:
        """Check if the ComfyUI server is reachable"""
        try:
            async with aiohttp.ClientSession() as session:
                start_time = asyncio.get_event_loop().time()
                async with session.get(
                    f"{self.base_url}/system_stats",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    end_time = asyncio.get_event_loop().time()
                    response_time_ms = (end_time - start_time) * 1000

                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "status": "available",
                            "response_time_ms": response_time_ms,
                            "system_info": data
                        }
                    else:
                        return {
                            "status": "error",
                            "response_time_ms": response_time_ms,
                            "error": f"HTTP {resp.status}"
                        }
        except asyncio.TimeoutError:
            return {
                "status": "offline",
                "error": "Connection timeout"
            }
        except aiohttp.ClientError as e:
            return {
                "status": "offline",
                "error": f"Connection error: {str(e)}"
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

    async def get_system_info(self) -> Dict[str, Any]:
        """Get system information from the ComfyUI server"""
        health = await self.health_check()

        info = {
            "engine": "comfyui",
            "host": self.host,
            "port": self.port,
            "secure": self.config.secure,
            "connected": health.get("status") == "available",
        }

        if info["connected"]:
            info["system_stats"] = health.get("system_info", {})
        else:
            info["error"] = health.get("error")

        return info
