"""Ollama plugin API routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from src.plugin_api import (
    AccountType,
    get_current_active_user,
)

from .client import get_settings, unload_models

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins/ollama", tags=["Ollama"])


@router.post("/actions/clear-vram")
async def clear_vram(current_user=Depends(get_current_active_user)):
    """Unload all currently loaded Ollama models.

    Strategy: GET /api/ps to list running models, then POST /api/generate
    with keep_alive=0 for each model name to evict it.
    """
    if current_user.account_type != AccountType.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")

    host, timeout = get_settings()
    result = await unload_models(host, timeout)

    if not result["connected"]:
        return {"success": False, "error": result["errors"][0] if result["errors"] else f"Cannot connect to Ollama at {host}"}

    unloaded = result["unloaded"]
    errors = result["errors"]
    if not unloaded and not errors:
        return {"success": True, "message": "No Ollama models were loaded.", "unloaded": []}

    logger.info("[OLLAMA] Cleared VRAM: unloaded=%s, errors=%s", unloaded, errors)
    suffix = f"; {len(errors)} failed" if errors else ""
    return {
        "success": len(errors) == 0,
        "message": f"Unloaded {len(unloaded)} model(s){suffix}",
        "unloaded": unloaded,
        "errors": errors,
    }
