"""ComfyUI Backend plugin API routes."""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.plugin_api import (
    PluginRepository,
    get_container,
    get_current_admin_user,
    lint_preset_dir,
)

from .preset_import.convert import extract_node_groups
from .preset_import.emit import FieldChoice, PresetEmitError, emit_preset
from .preset_import.parser import WorkflowFormatError, is_ui_format, parse_workflow
from .preset_import.suggest import suggest_fields

# How long to wait for a UI-format import's /object_info fetch - the same
# per-request timeout backend/requirements.py's requirement checkers use for
# calls to this same server.
_OBJECT_INFO_TIMEOUT_SECONDS = 5.0

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plugins/comfyui-backend", tags=["ComfyUI Backend"])

# content/presets/local is the .gitignored, user-owned preset root scanned
# exactly like content/presets/marketplace - see docs/presets.md.
_IMPORTED_PRESETS_ROOT = Path("content/presets/local")
_MARKETPLACE_PRESETS_ROOT = Path("content/presets/marketplace")


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


async def _fetch_object_info(base_url: str) -> Dict[str, Any]:
    """A live ComfyUI server's `GET /object_info`, needed to convert a
    UI-format workflow (see backend.preset_import.convert.convert_graph)
    and to enrich its field suggestions (backend.preset_import.suggest)."""
    timeout = aiohttp.ClientTimeout(total=_OBJECT_INFO_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{base_url}/object_info") as resp:
            resp.raise_for_status()
            return await resp.json()


async def _parse_incoming_workflow(raw_workflow: Dict[str, Any]) -> Tuple[Any, str, Optional[Dict[str, Any]]]:
    """Parse either workflow shape the import endpoints accept, returning
    `(workflow, format, object_info)` - `object_info` is the server response
    used to convert/enrich a UI-format workflow (`None` for an API-format
    one), so a caller needing it again (to enrich suggestions, or to record
    `object_info_used`) doesn't have to refetch it.

    A UI-format workflow needs a reachable ComfyUI backend to convert -
    unlike `parse_workflow`'s own "no object_info given" rejection (meant
    for a caller that never tries to fetch one), this is the "we tried and
    the backend wasn't there" case, so it gets its own message pointing at
    the two ways to unblock it."""
    ui_format = is_ui_format(raw_workflow)
    object_info: Optional[Dict[str, Any]] = None
    if ui_format:
        try:
            object_info = await _fetch_object_info(_get_comfyui_base_url())
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise WorkflowFormatError(
                "A reachable ComfyUI backend is needed to import UI-format workflows; "
                "use Export (API) or configure the backend"
            ) from e

    workflow = parse_workflow(raw_workflow, object_info=object_info)
    return workflow, ("ui" if ui_format else "api"), object_info


def _unknown_node_warnings(unknown_nodes: List[Dict[str, Any]]) -> List[str]:
    """One warning per distinct class in `Workflow.unknown_nodes` - a class
    /object_info didn't recognize still converts and imports (see
    backend.preset_import.convert's module docstring), this just tells the
    admin its fields are a best-effort guess until the node pack is
    installed and the workflow is re-imported."""
    seen: set = set()
    warnings: List[str] = []
    for entry in unknown_nodes:
        class_type = entry.get("class_type")
        if not class_type or class_type in seen:
            continue
        seen.add(class_type)
        warnings.append(
            f"Node '{class_type}' is not installed on the backend: its widget names were taken "
            "from the export / guessed — re-import after installing the pack"
        )
    return warnings


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


@router.get("/presets/families")
async def list_preset_families(current_user=Depends(get_current_admin_user)):
    """List the model-family directory names already in use under both preset
    roots, for the import UI's model-family field (suggestions only - any
    name may still be typed, see /presets/import)."""
    families = set()
    for root in (_MARKETPLACE_PRESETS_ROOT, _IMPORTED_PRESETS_ROOT):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                families.add(child.name)
    return {"families": sorted(families)}


@router.post("/presets/import/analyze")
async def analyze_workflow(
    body: AnalyzeWorkflowRequest, current_user=Depends(get_current_admin_user)
):
    """Parse a ComfyUI workflow - Export (API) or the UI's own Export/Save
    format - and suggest form fields for its configurable node inputs, for
    the import UI to let an admin tick which ones become preset form
    fields. A UI-format workflow additionally needs a reachable ComfyUI
    backend (fetched here, not cached) to map its widget values and enrich
    suggestions from the server's own /object_info."""
    try:
        workflow, workflow_format, object_info = await _parse_incoming_workflow(body.workflow)
    except WorkflowFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))

    node_groups = extract_node_groups(body.workflow) if workflow_format == "ui" else None
    analysis = suggest_fields(workflow, object_info=object_info, node_groups=node_groups)
    return {
        **analysis.to_dict(),
        "format": workflow_format,
        "object_info_used": object_info is not None,
        "unknown_nodes": workflow.unknown_nodes,
        "warnings": _unknown_node_warnings(workflow.unknown_nodes),
    }


@router.post("/presets/import")
async def import_workflow(
    body: ImportWorkflowRequest, current_user=Depends(get_current_admin_user)
):
    """Write a lint-clean preset directory under content/presets/local from a
    ComfyUI workflow - Export (API) or UI format - plus the admin's field
    choices (see /presets/import/analyze)."""
    try:
        workflow, workflow_format, object_info = await _parse_incoming_workflow(body.workflow)
    except WorkflowFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))

    choices = [FieldChoice(**choice.model_dump()) for choice in body.fields]
    ui_workflow = body.workflow if workflow_format == "ui" else None

    try:
        result = emit_preset(
            workflow,
            choices,
            model_family=body.model_family,
            variant=body.variant,
            display_name=body.display_name,
            dest_root=_IMPORTED_PRESETS_ROOT,
            object_info=object_info,
            ui_workflow=ui_workflow,
        )
    except PresetEmitError as e:
        raise HTTPException(status_code=400, detail=str(e))

    errors, warnings = lint_preset_dir(str(result.preset_dir))
    warnings = [*warnings, *_unknown_node_warnings(workflow.unknown_nodes)]

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
