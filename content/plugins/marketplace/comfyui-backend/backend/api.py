"""ComfyUI Backend plugin API routes."""

import logging
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends

from src.plugin_api import (
    PluginRepository,
    get_current_admin_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins/comfyui-backend", tags=["ComfyUI Backend"])


def _get_comfyui_base_url() -> str:
    """Build ComfyUI base URL from plugin settings."""
    repo = PluginRepository()
    settings = repo.get_plugin_settings("comfyui-backend")

    host = "127.0.0.1"
    port = 8188
    secure = False

    for s in settings:
        if s.setting_key == "default_host" and s.setting_value:
            host = s.setting_value
        elif s.setting_key == "default_port" and s.setting_value:
            try:
                port = int(s.setting_value)
            except ValueError:
                pass

    protocol = "https" if secure else "http"
    return f"{protocol}://{host}:{port}"


@router.post("/actions/clear-vram")
async def clear_vram(current_user=Depends(get_current_admin_user)):
    """Clear VRAM by calling ComfyUI POST /free endpoint.

    Sends both unload_models and free_memory flags to perform
    a thorough memory cleanup on the ComfyUI server.
    """
    base_url = _get_comfyui_base_url()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return {"success": True, "message": "VRAM cleared successfully"}
                else:
                    text = await resp.text()
                    logger.error("ComfyUI /free returned %d: %s", resp.status, text)
                    return {"success": False, "error": f"ComfyUI returned {resp.status}: {text}"}
    except aiohttp.ClientConnectorError:
        logger.error("Cannot connect to ComfyUI at %s", base_url)
        return {"success": False, "error": f"Cannot connect to ComfyUI at {base_url}"}
    except Exception as e:
        logger.error("Failed to clear VRAM: %s", e)
        return {"success": False, "error": str(e)}
