"""ComfyUI Backend plugin API routes."""

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.plugin_api import (
    PluginRepository,
    get_container,
    get_current_admin_user,
    lint_preset_dir,
)
from src.plugin_api.presets import RequirementContext

from .preset_import.convert import extract_node_groups
from .preset_import.emit import (
    IMPORT_PROVENANCE_PREFIX,
    FieldChoice,
    PresetEmitError,
    _infer_requirements,
    emit_preset,
)
from .preset_import.parser import WorkflowFormatError, is_ui_format, parse_workflow
from .preset_import.suggest import suggest_fields
from .requirements import ComfyUIModelChecker, ComfyUINodeChecker

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


@dataclass
class _ConfiguredBackendConfig:
    """Duck-types the `.config.get_base_url()` shape `backend.requirements`'s
    checkers read off `RequirementContext.backend` - built straight from this
    plugin's own settings (see `_get_comfyui_base_url`), not a registered
    core `Backend`, so the import wizard's Requirements step can preview
    checks before any preset (and therefore any backend resolution) exists."""

    base_url: str

    def get_base_url(self) -> str:
        return self.base_url


@dataclass
class _ConfiguredBackend:
    """Duck-types `RequirementBackendInfo` (`src.features.presets.requirements
    .contracts` - not re-exported through `src.plugin_api.presets`, so this
    plugin defines its own minimal shape rather than reach past the plugin
    API surface)."""

    id: str
    engine: str
    config: _ConfiguredBackendConfig


# How long the Requirements-preview step budgets for ALL of a workflow's
# inferred requirements together (they run concurrently) before any still
# pending resolve to "unknown" - matches `backend.requirements`'s own
# `_CHECK_TIMEOUT_SECONDS` per-entry ceiling for a big /object_info fetch.
_REQUIREMENTS_PREVIEW_BUDGET_SECONDS = 20.0

_REQUIREMENT_PREVIEW_CHECKERS = {
    "comfyui_node": ComfyUINodeChecker(),
    "comfyui_model": ComfyUIModelChecker(),
}


def _requirement_preview_name(checker: Any, entry: Dict[str, Any]) -> str:
    describe = getattr(checker, "describe", None) if checker else None
    if describe is not None:
        try:
            return describe(entry)
        except Exception:
            pass
    return entry.get("type", "requirement")


async def _check_one_requirement(entry: Dict[str, Any], ctx: RequirementContext) -> Dict[str, Any]:
    """One `_infer_requirements` entry, evaluated by the checker class this
    plugin itself registers for its `type:` (`comfyui_node`/`comfyui_model`) -
    reused directly rather than going through the generic preset-requirements
    evaluator, since a freshly-parsed workflow has no preset/backend
    resolution to route through yet."""
    req_type = entry.get("type", "")
    name = _requirement_preview_name(_REQUIREMENT_PREVIEW_CHECKERS.get(req_type), entry)
    checker = _REQUIREMENT_PREVIEW_CHECKERS.get(req_type)
    if checker is None:
        return {"type": req_type, "name": name, "status": "unknown", "detail": "No checker registered for this requirement type.", "hint": None}
    try:
        result = await checker.check(entry, ctx)
    except Exception as e:
        logger.exception("Requirement preview check failed for %s", entry)
        return {"type": req_type, "name": name, "status": "unknown", "detail": str(e), "hint": None}
    return {"type": req_type, "name": name, "status": result.status, "detail": result.detail, "hint": result.hint}


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


@router.post("/presets/import/requirements")
async def preview_workflow_requirements(
    body: AnalyzeWorkflowRequest, current_user=Depends(get_current_admin_user)
):
    """Preview the `requirements:` entries this workflow would get on import
    (see `preset_import.emit._infer_requirements`) and check each against
    this plugin's configured ComfyUI backend, independent of which inputs
    the admin has chosen as form fields - the import wizard's Requirements
    step, run before the preset itself exists so there is nothing yet for
    the core preset-requirements evaluator to check against."""
    try:
        workflow, _workflow_format, object_info = await _parse_incoming_workflow(body.workflow)
    except WorkflowFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))

    entries = _infer_requirements(workflow, object_info=object_info)
    ctx = RequirementContext(
        models=None,
        gpu_available=False,
        gpu_total_vram_gb=None,
        backend=_ConfiguredBackend(
            id="comfyui-backend:configured",
            engine="comfyui",
            config=_ConfiguredBackendConfig(base_url=_get_comfyui_base_url()),
        ),
        platform=sys.platform,
    )

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_check_one_requirement(entry, ctx) for entry in entries)),
            timeout=_REQUIREMENTS_PREVIEW_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        results = [
            {
                "type": entry.get("type", ""),
                "name": _requirement_preview_name(_REQUIREMENT_PREVIEW_CHECKERS.get(entry.get("type", "")), entry),
                "status": "unknown",
                "detail": "Timed out checking this requirement.",
                "hint": None,
            }
            for entry in entries
        ]

    return {"results": results}


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


async def _peek_requirements_summary(preset_id: str) -> Optional[Dict[str, int]]:
    """The preset's last-evaluated `requirements_summary`, straight from the
    same controller `GET /api/presets/{id}` calls - never a live check (see
    `src.features.presets.operations.query._peek_requirements_summary`)."""
    try:
        response = await get_container().preset_controller.get_preset(preset_id)
    except Exception:
        return None
    if not response.success or not response.data:
        return None
    return response.data.get("requirements_summary")


@router.get("/presets/imported")
async def list_imported_presets(current_user=Depends(get_current_admin_user)):
    """Presets under content/presets/local this plugin created - identified by
    `description.md`'s opening line (see `preset_import.emit.emit_preset`),
    the only provenance marker an imported preset carries."""
    presets: List[Dict[str, Any]] = []

    if _IMPORTED_PRESETS_ROOT.is_dir():
        for family_dir in sorted(p for p in _IMPORTED_PRESETS_ROOT.iterdir() if p.is_dir()):
            for variant_dir in sorted(p for p in family_dir.iterdir() if p.is_dir()):
                preset_yml_path = variant_dir / "preset.yml"
                description_path = variant_dir / "description.md"
                if not preset_yml_path.is_file() or not description_path.is_file():
                    continue
                try:
                    if not description_path.read_text(encoding="utf-8").startswith(IMPORT_PROVENANCE_PREFIX):
                        continue
                    preset_yml = yaml.safe_load(preset_yml_path.read_text(encoding="utf-8")) or {}
                except (OSError, yaml.YAMLError):
                    continue

                preset_id = preset_yml.get("id")
                modes = preset_yml.get("modes") or []
                mode = modes[0] if modes else None
                workflow_format = "unknown"
                if mode:
                    workflows_dir = variant_dir / "modes" / mode / "files" / "workflows"
                    if (workflows_dir / f"{mode}.ui.json").is_file():
                        workflow_format = "ui"
                    elif (workflows_dir / f"{mode}.json").is_file():
                        workflow_format = "api"

                presets.append({
                    "preset_id": preset_id,
                    "name": preset_yml.get("name", variant_dir.name),
                    "family": family_dir.name,
                    "variant": variant_dir.name,
                    "format": workflow_format,
                    "requirements_summary": await _peek_requirements_summary(preset_id) if preset_id else None,
                    "created_at": preset_yml_path.stat().st_mtime,
                })

    presets.sort(key=lambda p: p["created_at"], reverse=True)
    return {"presets": presets}
