"""ComfyUI Backend plugin API routes."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.plugin_api import (
    PluginRepository,
    get_container,
    get_current_admin_user,
    lint_preset_dir,
)

from .preset_import.emit import FieldChoice, PresetEmitError, emit_preset
from .preset_import.parser import WorkflowFormatError, parse_api_workflow
from .preset_import.suggest import suggest_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins/comfyui-backend", tags=["ComfyUI Backend"])

# content/presets/local is the .gitignored, user-owned preset root scanned
# exactly like content/presets/marketplace - see docs/presets.md.
_IMPORTED_PRESETS_ROOT = Path("content/presets/local")


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


class AnalyzeWorkflowRequest(BaseModel):
    workflow: Dict[str, Any]


class ImportFieldChoice(BaseModel):
    node_id: str
    input_name: str
    field_name: Optional[str] = None
    field_type: Optional[str] = None
    label: Optional[str] = None


class ImportWorkflowRequest(BaseModel):
    workflow: Dict[str, Any]
    fields: List[ImportFieldChoice] = []
    model_family: str
    variant: str = "imported"
    display_name: str


@router.post("/presets/import/analyze")
async def analyze_workflow(
    body: AnalyzeWorkflowRequest, current_user=Depends(get_current_admin_user)
):
    """Parse an Export (API) ComfyUI workflow and suggest form fields for its
    configurable node inputs, for the import UI to let an admin tick which
    ones become preset form fields."""
    try:
        workflow = parse_api_workflow(body.workflow)
    except WorkflowFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))

    analysis = suggest_fields(workflow)
    return analysis.to_dict()


@router.post("/presets/import")
async def import_workflow(
    body: ImportWorkflowRequest, current_user=Depends(get_current_admin_user)
):
    """Write a lint-clean preset directory under content/presets/local from an
    Export (API) ComfyUI workflow plus the admin's field choices (see
    /presets/import/analyze)."""
    try:
        workflow = parse_api_workflow(body.workflow)
    except WorkflowFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))

    choices = [FieldChoice(**choice.model_dump()) for choice in body.fields]

    try:
        result = emit_preset(
            workflow,
            choices,
            model_family=body.model_family,
            variant=body.variant,
            display_name=body.display_name,
            dest_root=_IMPORTED_PRESETS_ROOT,
        )
    except PresetEmitError as e:
        raise HTTPException(status_code=400, detail=str(e))

    errors, warnings = lint_preset_dir(str(result.preset_dir))

    try:
        get_container().preset_template_loader.reload()
    except Exception:
        logger.exception("Failed to reload the preset catalogue after importing %s", result.preset_id)

    return {
        "preset_id": result.preset_id,
        "path": str(result.preset_dir),
        "mode": result.mode,
        "lint": {"errors": errors, "warnings": warnings},
    }
