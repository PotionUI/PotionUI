"""ComfyUI Backend plugin API routes."""

import asyncio
import json
import logging
import shutil
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

from .preset_import.defaults import build_default_form, build_default_history
from .preset_import.emit import (
    IMPORT_PROVENANCE_PREFIX,
    IMPORT_SIDECAR_FILENAME,
    IMPORT_SOURCE_FILENAME,
    PresetEmitError,
    _infer_requirements,
    emit_preset,
    form_driven_model_inputs,
)
from .preset_import.parser import Workflow, WorkflowFormatError, parse_api_workflow
from .preset_import.schema import ImportForm, parse_form, parse_history
from .preset_import.suggest import AnalyzeResult, classification_fingerprint, suggest_fields
from .requirements import ComfyUIModelChecker, ComfyUINodeChecker, _fetch_object_info

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


def _parse_workflow(raw_workflow: Dict[str, Any]) -> Workflow:
    """Parse a ComfyUI Export (API) workflow for the import endpoints below,
    raising an HTTP 400 with a teaching message for the ComfyUI UI export
    (or anything else that isn't a recognizable ComfyUI workflow) - see
    `parser.parse_api_workflow`."""
    try:
        return parse_api_workflow(raw_workflow)
    except WorkflowFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _resolve_workflow_json(workflow: Dict[str, Any], workflow_text: Optional[str]) -> Dict[str, Any]:
    """The dict every import endpoint actually parses: `workflow_text` (see
    `AnalyzeWorkflowRequest.workflow_text`) when the caller sent one, else
    the plain `workflow` dict, unchanged, for a caller that didn't. The
    `json` stdlib decoder's default integer parsing is already exact for an
    arbitrarily large literal (only a value with a decimal point or exponent
    becomes a `float`) - no `parse_int` override needed, just a decoder that
    never touched JavaScript."""
    if workflow_text is None:
        return workflow
    try:
        parsed = json.loads(workflow_text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"workflow_text is not valid JSON: {e}")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="workflow_text must decode to a JSON object.")
    return parsed


# JavaScript's `Number` is an IEEE-754 double: an integer literal is only
# exactly representable up to this magnitude (`Number.MAX_SAFE_INTEGER`,
# 2**53-1) - anything beyond it silently rounds the instant a browser's own
# `JSON.parse` touches it. Every response below that might echo a literal
# value straight out of a parsed workflow (a candidate's `current_value`, a
# generated field's `default`, the stored workflow dict itself) is walked
# through `_make_json_safe` before it leaves this process, so an oversized
# int is never handed to the client as a raw JSON number for its own
# JSON.parse to mangle.
_JS_MAX_SAFE_INTEGER = (1 << 53) - 1


def _make_json_safe(value: Any) -> Any:
    """Recursively replace any `int` outside JS's safe integer range with
    `{"__exact_int__": "<digits>"}` - a plain JSON string survives a
    browser's JSON.parse byte-for-byte, unlike a JSON number in that range.
    `bool` is checked first since `bool` is an `int` subclass in Python.
    Not a regex/string rewrite: this walks the already-decoded Python value
    tree (ints are still real Python ints here, exact at any size), so it
    can never misidentify a numeric-looking string or mis-tag a value nested
    inside one."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not (-_JS_MAX_SAFE_INTEGER <= value <= _JS_MAX_SAFE_INTEGER):
        return {"__exact_int__": str(value)}
    if isinstance(value, dict):
        return {key: _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_make_json_safe(item) for item in value]
    return value


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

# `backend.requirements`'s checkers key their single-flight `/object_info`
# cache by resolved backend id; there is no real `Backend` at analyze/source
# time (see `_ConfiguredBackend`), so this plugin's own settings stand in
# under a fixed id - shared with `preview_workflow_requirements` below so the
# two steps never fetch the same multi-MB listing twice.
_CONFIGURED_BACKEND_ID = "comfyui-backend:configured"

# Best-effort budget for analyze/source's enrichment fetch - short enough
# that an unreachable/slow ComfyUI server never meaningfully delays opening
# the wizard (it just imports without enrichment, same as no backend at all).
_ANALYZE_OBJECT_INFO_TIMEOUT_SECONDS = 5.0


async def _try_object_info() -> Optional[Dict[str, Any]]:
    """Best-effort live `/object_info` for `/presets/import/analyze` and
    `.../presets/imported/{id}/source`'s enrichment pass (real min/max/step,
    live combo options, a resolved node's actual output types - see
    `suggest._enrich_with_object_info`) - `None` on any failure (unreachable
    backend, timeout), never raised: importing a plain Export (API) workflow
    with no ComfyUI backend configured at all must still work exactly as
    before."""
    base_url = _get_comfyui_base_url()
    try:
        return await asyncio.wait_for(
            _fetch_object_info(_CONFIGURED_BACKEND_ID, base_url),
            timeout=_ANALYZE_OBJECT_INFO_TIMEOUT_SECONDS,
        )
    except Exception:
        return None


@dataclass
class _ResolvedAnalysis:
    """One `_try_object_info()` fetch and the `suggest_fields` analysis built
    from it - every endpoint below that classifies a workflow's inputs
    (analyze, source, requirements-preview, import, reload) resolves through
    `_resolve_analysis` instead of fetching/analyzing independently, so two
    of them can never end up classifying the same input differently within
    one request (see `emit._check_schema_drift`, which then compares this
    across *requests*)."""

    object_info: Optional[Dict[str, Any]]
    analysis: AnalyzeResult

    @property
    def fingerprint(self) -> str:
        return classification_fingerprint(self.analysis)


async def _resolve_analysis(workflow: Workflow) -> _ResolvedAnalysis:
    object_info = await _try_object_info()
    analysis = suggest_fields(workflow, object_info=object_info)
    return _ResolvedAnalysis(object_info=object_info, analysis=analysis)


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
    # The exact source text of the same workflow - byte-for-byte as pasted or
    # uploaded, never round-tripped through the browser's own JSON.parse/
    # JSON.stringify. `workflow` above has already made that round trip by
    # the time it reaches this request body, which silently rounds any
    # integer literal outside JS's safe range (Number.MAX_SAFE_INTEGER,
    # +/-(2**53-1)) to the nearest representable double - a workflow seed or
    # other large literal is exactly the kind of value that lands there.
    # `workflow_text`, when given, is authoritative over `workflow` for
    # every literal value: it is re-decoded server-side with the stdlib
    # `json` module, whose default integer parsing keeps an arbitrarily
    # large literal exact (see `_resolve_workflow_json`). Optional and
    # purely additive - an existing dict-only client (this field didn't
    # exist before) is unaffected and keeps working exactly as before.
    workflow_text: Optional[str] = None


class RequirementsPreviewRequest(AnalyzeWorkflowRequest):
    # The wizard's current form, so the preview omits the model inputs a
    # picker drives exactly as the emitted preset will - see
    # `emit.form_driven_model_inputs`.
    form: Optional[Dict[str, Any]] = None


class ImportWorkflowRequest(BaseModel):
    workflow: Dict[str, Any]
    # See `AnalyzeWorkflowRequest.workflow_text` - same authoritative-when-
    # present contract, resolved by the same `_resolve_workflow_json`.
    workflow_text: Optional[str] = None
    # Raw dicts, not typed as ImportForm/List[HistoryEntry] directly: a bad
    # shape here is turned into a 400 with a clear message by
    # `schema.parse_form`/`parse_history` (see the module's own
    # PresetEmitError), not FastAPI's generic 422.
    form: Optional[Dict[str, Any]] = None
    history: List[Dict[str, Any]] = []
    model_family: str
    variant: str = "imported"
    display_name: str
    # Set by the wizard's "Update preset" (edit) path: re-emit into the same
    # directory under this same id instead of refusing an existing one - see
    # emit_preset's overwrite/preset_id.
    overwrite_preset_id: Optional[str] = None
    # Echoed back from analyze/source's own `schema_fingerprint`/
    # `object_info_used` - never trusted as a declared role, only compared
    # against a freshly-resolved analysis to refuse a save whose workflow
    # classification drifted since the client was shown it (see
    # emit._check_schema_drift). `None` (a caller that never analyzed
    # through this session, or an older client) skips the check entirely.
    schema_fingerprint: Optional[str] = None
    schema_object_info_used: Optional[bool] = None


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
    """Parse a ComfyUI Export (API) workflow and suggest form fields for its
    configurable node inputs, for the import UI to let an admin tick which
    ones become preset form fields."""
    workflow = _parse_workflow(_resolve_workflow_json(body.workflow, body.workflow_text))

    resolved = await _resolve_analysis(workflow)
    default_form = build_default_form(resolved.analysis)
    default_history = build_default_history(default_form, resolved.analysis)
    return _make_json_safe({
        **resolved.analysis.to_dict(),
        "format": "api",
        "object_info_used": resolved.object_info is not None,
        "schema_fingerprint": resolved.fingerprint,
        "default_form": default_form.model_dump(mode="json"),
        "default_history": [entry.model_dump(mode="json") for entry in default_history],
    })


@router.post("/presets/import/requirements")
async def preview_workflow_requirements(
    body: RequirementsPreviewRequest, current_user=Depends(get_current_admin_user)
):
    """Preview the `requirements:` entries this workflow would get on import
    (see `preset_import.emit._infer_requirements`) and check each against
    this plugin's configured ComfyUI backend, independent of which inputs
    the admin has chosen as form fields - the import wizard's Requirements
    step, run before the preset itself exists so there is nothing yet for
    the core preset-requirements evaluator to check against."""
    workflow = _parse_workflow(_resolve_workflow_json(body.workflow, body.workflow_text))

    resolved = await _resolve_analysis(workflow)
    try:
        form = parse_form(body.form) if body.form else None
    except PresetEmitError as e:
        raise HTTPException(status_code=400, detail=str(e))
    entries = _infer_requirements(
        workflow,
        object_info=resolved.object_info,
        candidates=resolved.analysis.candidates,
        form_driven_inputs=form_driven_model_inputs(form) if form else None,
    )
    ctx = RequirementContext(
        models=None,
        gpu_available=False,
        gpu_total_vram_gb=None,
        backend=_ConfiguredBackend(
            id=_CONFIGURED_BACKEND_ID,
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
    ComfyUI Export (API) workflow plus the admin's `form`/`history` (see
    /presets/import/analyze's default_form/default_history)."""
    workflow = _parse_workflow(_resolve_workflow_json(body.workflow, body.workflow_text))
    resolved = await _resolve_analysis(workflow)

    try:
        form = parse_form(body.form)
        history = parse_history(body.history)
        result = emit_preset(
            workflow,
            form,
            history,
            model_family=body.model_family,
            variant=body.variant,
            display_name=body.display_name,
            dest_root=_IMPORTED_PRESETS_ROOT,
            object_info=resolved.object_info,
            expected_schema_fingerprint=body.schema_fingerprint,
            expected_object_info_used=body.schema_object_info_used,
            overwrite=bool(body.overwrite_preset_id),
            preset_id=body.overwrite_preset_id,
            source_text=body.workflow_text,
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


@dataclass
class _ImportedPresetEntry:
    """One directory under content/presets/local this importer created,
    resolved once by `_scan_imported_presets` and reused by the list,
    source, reload, and delete endpoints below - the single place that
    walks the tree, reads preset.yml/description.md/import.json, and
    decides "did this importer make this" so the four endpoints can never
    disagree about it."""

    dir: Path
    family: str
    variant: str
    preset_id: Optional[str]
    preset_yml: Dict[str, Any]
    mode: Optional[str]
    workflow_format: str
    sidecar: Optional[Dict[str, Any]]
    mtime: float

    @property
    def has_sidecar(self) -> bool:
        return self.sidecar is not None

    @property
    def source_workflow_path(self) -> Path:
        return self.dir / IMPORT_SOURCE_FILENAME

    @property
    def has_source_workflow(self) -> bool:
        """Whether the exact imported workflow is stored - see `_parse_stored_form`."""
        return self.source_workflow_path.is_file()


def _scan_imported_presets():
    """Yields `_ImportedPresetEntry` for every content/presets/local
    directory carrying this importer's provenance marker - a preset is
    "imported" by that marker alone (see `IMPORT_PROVENANCE_PREFIX`), the
    sidecar is an optional enrichment on top (presets imported before it
    existed simply have `sidecar=None`)."""
    if not _IMPORTED_PRESETS_ROOT.is_dir():
        return

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

            modes = preset_yml.get("modes") or []
            mode = modes[0] if modes else None
            workflow_format = "unknown"
            if mode:
                workflows_dir = variant_dir / "modes" / mode / "files" / "workflows"
                if (workflows_dir / f"{mode}.ui.json").is_file():
                    workflow_format = "ui"
                elif (workflows_dir / f"{mode}.json").is_file():
                    workflow_format = "api"

            sidecar = None
            sidecar_path = variant_dir / IMPORT_SIDECAR_FILENAME
            if sidecar_path.is_file():
                try:
                    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    sidecar = None

            yield _ImportedPresetEntry(
                dir=variant_dir,
                family=family_dir.name,
                variant=variant_dir.name,
                preset_id=preset_yml.get("id"),
                preset_yml=preset_yml,
                mode=mode,
                workflow_format=workflow_format,
                sidecar=sidecar,
                mtime=preset_yml_path.stat().st_mtime,
            )


def _find_imported_preset(preset_id: str) -> Optional[_ImportedPresetEntry]:
    for entry in _scan_imported_presets():
        if entry.preset_id == preset_id:
            return entry
    return None


def _resolve_stored_workflow_path(entry: _ImportedPresetEntry) -> Path:
    """The workflow file this preset was built from: the exact imported
    source (`IMPORT_SOURCE_FILENAME`) when the emitter stored one, else the
    emitted `<mode>.json`. A preset imported before this importer dropped
    UI-format support may still have a kept `<mode>.ui.json` alongside the
    converted `<mode>.json` - that file is still preferred over the emitted
    one (same historical source the preset was built from), but
    `_parse_workflow` now rejects it with the same teaching error a fresh
    UI-format upload gets, since there is no conversion path left to run it
    through."""
    if entry.has_source_workflow:
        return entry.source_workflow_path
    if entry.mode is None:
        raise HTTPException(status_code=400, detail="This preset has no recorded mode to reload from.")
    workflows_dir = entry.dir / "modes" / entry.mode / "files" / "workflows"
    ui_path = workflows_dir / f"{entry.mode}.ui.json"
    api_path = workflows_dir / f"{entry.mode}.json"
    source_path = ui_path if ui_path.is_file() else api_path
    if not source_path.is_file():
        raise HTTPException(status_code=404, detail="This preset's source workflow file is missing.")
    return source_path


def _read_stored_workflow_text(entry: _ImportedPresetEntry) -> str:
    """The stored workflow file's exact text - never round-tripped through
    `json.loads`/`json.dumps`, so an integer literal outside JS's safe range
    survives verbatim. This is what `/source` hands back as `workflow_text`
    for the wizard's edit/modify flow to resubmit unchanged, instead of
    re-`JSON.stringify`ing an already-rounded parsed object (see
    `_make_json_safe` / docs/presets.md's "Exact large integers" note)."""
    source_path = _resolve_stored_workflow_path(entry)
    try:
        return source_path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Could not read the stored workflow: {e}")


def _read_stored_workflow(entry: _ImportedPresetEntry) -> Tuple[str, Dict[str, Any]]:
    """`(exact_text, parsed)` of the workflow this preset was built from -
    the text goes back out as `workflow_text` (`/source`) or into the
    re-emitted preset's source file (reload) unchanged; the parsed dict is
    what gets analyzed."""
    text = _read_stored_workflow_text(entry)
    try:
        return text, json.loads(text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Could not read the stored workflow: {e}")


def _parse_stored_form(entry: _ImportedPresetEntry, stored_form: Dict[str, Any]) -> ImportForm:
    """The sidecar's `form`; without `IMPORT_SOURCE_FILENAME` the re-opened
    workflow is the emitted one, which no longer holds the replaced LoRA
    nodes, so `lora_chain.replaced_node_ids` is dropped."""
    form = parse_form(stored_form)
    if entry.has_source_workflow or form.lora_chain is None:
        return form
    return form.model_copy(update={"lora_chain": form.lora_chain.model_copy(update={"replaced_node_ids": []})})


@router.get("/presets/imported")
async def list_imported_presets(current_user=Depends(get_current_admin_user)):
    """Presets under content/presets/local this plugin created - identified by
    `description.md`'s opening line (see `preset_import.emit.emit_preset`),
    the only provenance marker an imported preset carries."""
    entries = list(_scan_imported_presets())
    presets = [
        {
            "preset_id": entry.preset_id,
            "name": entry.preset_yml.get("name", entry.variant),
            "family": entry.family,
            "variant": entry.variant,
            "format": entry.workflow_format,
            "has_sidecar": entry.has_sidecar,
            "requirements_summary": await _peek_requirements_summary(entry.preset_id) if entry.preset_id else None,
            "created_at": entry.mtime,
        }
        for entry in entries
    ]
    presets.sort(key=lambda p: p["created_at"], reverse=True)
    return {"presets": presets}


@router.get("/presets/imported/{preset_id}/source")
async def get_imported_preset_source(preset_id: str, current_user=Depends(get_current_admin_user)):
    """The stored workflow plus a fresh analysis of it (same envelope as
    `/presets/import/analyze`), enriched with this preset's own identity and
    its sidecar's `form`/`history` - what the wizard's "edit" entry point
    needs to reopen an imported preset exactly as it was built, in one round
    trip. A preset imported before the sidecar existed (or before it carried
    `form`/`history`) falls back to `default_form`/`default_history`, same as
    a brand-new analyze."""
    entry = _find_imported_preset(preset_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Imported preset not found.")

    workflow_text, raw_workflow = _read_stored_workflow(entry)
    workflow = _parse_workflow(raw_workflow)

    resolved = await _resolve_analysis(workflow)
    analysis = resolved.analysis

    stored_form = entry.sidecar.get("form") if entry.sidecar else None
    stored_history = entry.sidecar.get("history") if entry.sidecar else None
    try:
        form = _parse_stored_form(entry, stored_form) if stored_form else build_default_form(analysis)
        history = (
            parse_history(stored_history)
            if stored_history is not None
            else build_default_history(form, analysis)
        )
    except PresetEmitError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _make_json_safe({
        "workflow": raw_workflow,
        # The exact stored bytes, for the wizard to keep and resubmit as
        # `workflow_text` on "Update preset" instead of re-serializing
        # `workflow` above (which this same response has already tagged any
        # oversized literal in) - see `_read_stored_workflow_text`.
        "workflow_text": workflow_text,
        **analysis.to_dict(),
        "format": "api",
        "object_info_used": resolved.object_info is not None,
        "schema_fingerprint": resolved.fingerprint,
        "model_family": entry.family,
        "variant": entry.variant,
        "display_name": entry.preset_yml.get("name", entry.variant),
        "form": form.model_dump(mode="json"),
        "history": [e.model_dump(mode="json") for e in history],
    })


@router.post("/presets/imported/{preset_id}/reload")
async def reload_imported_preset(preset_id: str, current_user=Depends(get_current_admin_user)):
    """Re-parse this preset's stored source workflow and re-emit it into the
    SAME directory under the SAME id, using its sidecar's exact `form`/
    `history` when it has one - "pick up a workflow/importer change without
    re-designing the form". A preset imported before the sidecar carried
    `form`/`history` falls back to the default form/history and says so in
    `warnings`, same as a brand-new import would produce today."""
    entry = _find_imported_preset(preset_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Imported preset not found.")

    workflow_text, raw_workflow = _read_stored_workflow(entry)
    workflow = _parse_workflow(raw_workflow)
    resolved = await _resolve_analysis(workflow)

    extra_warnings: List[str] = []
    stored_form = entry.sidecar.get("form") if entry.sidecar else None
    stored_history = entry.sidecar.get("history") if entry.sidecar else None
    # A sidecar written before schema-drift checking existed simply lacks
    # these keys - `_check_schema_drift` treats `None` as "no baseline",
    # same as the has_sidecar=False case just below.
    expected_schema_fingerprint = entry.sidecar.get("schema_fingerprint") if entry.sidecar else None
    expected_object_info_used = entry.sidecar.get("schema_object_info_used") if entry.sidecar else None
    try:
        if stored_form is not None:
            form = _parse_stored_form(entry, stored_form)
            history = parse_history(stored_history or [])
        else:
            form = build_default_form(resolved.analysis)
            history = build_default_history(form, resolved.analysis)
            extra_warnings.append("re-imported with the default form/history")

        result = emit_preset(
            workflow,
            form,
            history,
            model_family=entry.family,
            variant=entry.variant,
            display_name=entry.preset_yml.get("name", entry.variant),
            dest_root=_IMPORTED_PRESETS_ROOT,
            object_info=resolved.object_info,
            expected_schema_fingerprint=expected_schema_fingerprint,
            expected_object_info_used=expected_object_info_used,
            overwrite=True,
            preset_id=preset_id,
            source_text=workflow_text,
        )
    except PresetEmitError as e:
        raise HTTPException(status_code=400, detail=str(e))

    errors, warnings = lint_preset_dir(str(result.preset_dir))
    warnings = [*warnings, *extra_warnings]

    try:
        get_container().preset_template_loader.reload()
    except Exception:
        logger.exception("Failed to reload the preset catalogue after reloading %s", preset_id)

    return {
        "preset_id": result.preset_id,
        "path": str(result.preset_dir),
        "mode": result.mode,
        "lint": {"errors": errors, "warnings": warnings},
    }


@router.delete("/presets/imported/{preset_id}")
async def delete_imported_preset(preset_id: str, current_user=Depends(get_current_admin_user)):
    """Remove an imported preset's directory entirely. Only ever a preset
    `_scan_imported_presets` itself found (this importer's own provenance
    marker, under content/presets/local) - `preset_id` never builds a path
    directly, so there is no traversal surface from it, but the resolved
    directory is still asserted inside the imported-presets root before
    deleting it, matching emit_preset's own belt-and-braces check."""
    entry = _find_imported_preset(preset_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Imported preset not found.")

    root_resolved = _IMPORTED_PRESETS_ROOT.resolve()
    dir_resolved = entry.dir.resolve()
    if root_resolved not in dir_resolved.parents:
        raise HTTPException(status_code=400, detail="Refusing to delete a directory outside the imported presets root.")

    shutil.rmtree(dir_resolved)

    try:
        get_container().preset_template_loader.reload()
    except Exception:
        logger.exception("Failed to reload the preset catalogue after deleting %s", preset_id)

    return {"deleted": True, "preset_id": preset_id}
