"""Presets and starting a generation.

`FilePresetRepository` reads the installed presets, so a plugin can find the
preset it wants to run. `src.features.presets.operations` and the
`PresetCollaborators` bundle it dispatches onto (see
`src.features.presets.collaborators`) hold the rest of the preset business
logic (install/assign/configure), for a plugin that needs more than a lookup.

To actually generate, build a `GenerationRequest` - the preset, the mode, the
`PromptPair`s and the form data its fields expect - and hand it to the generation
orchestrator from the container:

    orchestrator = get_container().generation_orchestrator
    result = await orchestrator.start_generation(
        request, user.id, acts_as_admin=user.account_type == AccountType.ADMIN
    )

A plugin that writes a preset directory to disk (e.g. a workflow importer) can
validate it with `lint_preset_dir` - the same `PresetLinter` backing
`scripts/preset_lint.py` and `GET /api/developer/presets/lint` - without
shelling out to a script. To make a freshly written preset visible without a
restart, reload the running catalogue via
`get_container().preset_template_loader.reload()` (see `src.plugin_api.hooks`).

A plugin can also contribute a preset **requirement checker** - the code that
evaluates one `requirements:` entry `type:` (see docs/presets.md
"Requirements") against this instance, e.g. a ComfyUI custom-node check.
Declare it under `requirement_checkers:` in `manifest.yml`, pointing `backend`
at a class implementing `RequirementChecker` below:

    requirement_checkers:
      - type: comfyui_node
        backend: checkers:ComfyNodeChecker

`RequirementChecker.check()` receives the raw `requirements:` entry dict and a
`RequirementContext` (the models catalog, GPU info, the preset's resolved
backend, this host's platform) and returns a `RequirementResult` - never
"missing" when you simply couldn't tell; return `status="unknown"` instead.

See docs/presets.md.
"""

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.features.generation.dto import GenerationRequest, PromptPair
from src.features.generation.policy import GenerationPolicy
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets import operations as preset_operations
from src.features.presets.children_paths import resolve_children_path
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.linter import (
    COVER_VIDEO_MAX_BYTES as PRESET_COVER_VIDEO_MAX_BYTES,
    MEDIA_MAX_BYTES as PRESET_MEDIA_MAX_BYTES,
    PresetLinter,
)
from src.features.presets.requirements.contracts import (
    RequirementAction,
    RequirementChecker,
    RequirementBackendInfo,
    RequirementContext,
    RequirementResult,
)
from src.features.presets.schema import GalleryItem, PresetMedia
from src.features.presets.style_previews import downscale_and_save_webp
from src.features.presets.templates import PresetTemplate, default_form_name, files_preset_id
from src.features.video_director import apply_preset_mode_overlay
from src.features.video_director.binding import REFERENCE_ROLE, DirectorBindingError
from src.features.video_director.binding import bind_director_media as _bind_director_media
from src.platform.plugins.runtime_registries import get_container

__all__ = [
    "DirectorBindingError",
    "FieldDescription",
    "FilePresetRepository",
    "GalleryItem",
    "GenerationRequest",
    "ModeDescription",
    "PresetCollaborators",
    "PRESET_COVER_VIDEO_MAX_BYTES",
    "PRESET_MEDIA_MAX_BYTES",
    "PresetDescription",
    "PresetInstallState",
    "PresetMedia",
    "PresetTemplate",
    "RequirementAction",
    "RequirementChecker",
    "RequirementBackendInfo",
    "RequirementContext",
    "RequirementResult",
    "bind_director_media",
    "describe_preset",
    "downscale_and_save_webp",
    "files_preset_id",
    "lint_preset_dir",
    "list_presets",
    "preset_install_state",
    "preset_operations",
    "preset_template",
    "PromptPair",
    "resolve_preset_path",
]


def lint_preset_dir(path: str) -> Tuple[List[str], List[str]]:
    """Lint a single preset directory (containing a `preset.yml`).

    Returns `(errors, warnings)` as formatted strings, exactly like the issues
    `scripts/preset_lint.py` prints and `GET /api/developer/presets/lint`
    returns. Errors mean the preset will fail to load.

    Validates `requirements:` against the live, process-wide requirement
    checker registry when the app is running - a plugin enabled at runtime
    (not just at boot) registers its checkers there. Falls back to no
    registry override (core checkers only) when called before the container
    exists, e.g. a plugin's own unit tests calling this without booting the app.
    """
    try:
        registry = get_container().requirement_checker_registry
    except RuntimeError:
        registry = None
    issues = PresetLinter([str(path)], requirement_checker_registry=registry).lint()
    errors = [str(issue) for issue in issues if issue.level == "error"]
    warnings = [str(issue) for issue in issues if issue.level == "warning"]
    return errors, warnings


def _request_field(request: Any, name: str) -> Any:
    return request.get(name) if isinstance(request, Mapping) else getattr(request, name, None)


def _preset_capabilities(request: Any, role: str) -> Optional[Dict[str, Any]]:
    form_data = _request_field(request, "form_data")
    document = form_data.get("video_director") if isinstance(form_data, Mapping) else None
    if role != REFERENCE_ROLE and (not isinstance(document, Mapping) or document.get("mode") != "director"):
        return None
    repository = get_container().file_preset_repository
    preset_id = _request_field(request, "preset_id")
    template = repository.find_preset_by_id(preset_id) if preset_id else None
    capabilities = (template.vars or {}).get("video_director") if template is not None else None
    if not isinstance(capabilities, Mapping):
        return {}
    return apply_preset_mode_overlay(capabilities, _request_field(request, "mode"))


def bind_director_media(
    request,
    role: str,
    media: Any,
    segment_id: Optional[str] = None,
    promote: bool = False,
    *,
    at: Optional[float] = None,
    capabilities: Optional[Mapping[str, Any]] = None,
    field: Optional[str] = None,
):
    if capabilities is None:
        capabilities = _preset_capabilities(request, role)
    return _bind_director_media(
        request, role, media, segment_id, promote, at=at, capabilities=capabilities, field=field,
    )


@dataclass(frozen=True)
class FieldDescription:
    type: Optional[str]
    required: bool
    multi: bool = False


@dataclass(frozen=True)
class ModeDescription:
    default_form: Optional[str]
    forms: Dict[str, Dict[str, FieldDescription]] = field(default_factory=dict)


@dataclass(frozen=True)
class PresetDescription:
    id: str
    name: str
    vars: Dict[str, Any] = field(default_factory=dict)
    modes: Dict[str, ModeDescription] = field(default_factory=dict)
    engine: Optional[str] = None
    category: Optional[str] = None
    requires: Optional[Dict[str, Any]] = None


def _describe_fields(fields: Iterable[Any], out: Dict[str, FieldDescription]) -> None:
    for form_field in fields or []:
        if form_field.name and form_field.name not in out:
            out[form_field.name] = FieldDescription(
                type=form_field.type,
                required=bool(form_field.required),
                multi=bool((form_field.configuration or {}).get("multi")),
            )
        if isinstance(form_field.children, list):
            _describe_fields(form_field.children, out)


@dataclass(frozen=True)
class PresetInstallState:
    installed: bool
    assigned: bool


def preset_template(preset_id: str) -> Optional[PresetTemplate]:
    template = get_container().file_preset_repository.find_preset_by_id(preset_id)
    return copy.deepcopy(template) if template is not None else None


def resolve_preset_path(preset_id: str, reference: str) -> Optional[Path]:
    repository = get_container().file_preset_repository
    template = repository.find_preset_by_id(preset_id)
    if template is None:
        return None
    preset_root = Path(template.path)
    shared_root = Path(repository.preset_loader.shared_path)
    try:
        resolved = resolve_children_path(reference, preset_root, shared_root).resolve()
        roots = [preset_root.resolve(), shared_root.resolve()]
    except (OSError, RuntimeError, ValueError):
        return None
    if not any(resolved.is_relative_to(root) for root in roots):
        return None
    return resolved


def preset_install_state(preset_id: str) -> PresetInstallState:
    summary = get_container().preset_collaborators.db_repo.get_installed_preset_summaries().get(preset_id)
    if summary is None:
        return PresetInstallState(installed=False, assigned=False)
    _, users, groups = summary
    return PresetInstallState(installed=True, assigned=users + groups > 0)


def describe_preset(preset_id: str) -> Optional[PresetDescription]:
    template = get_container().file_preset_repository.find_preset_by_id(preset_id)
    if template is None:
        return None
    return _describe(template)


def list_presets(user) -> List[PresetDescription]:
    if user is None:
        raise PermissionError("A user is required to list presets")
    container = get_container()
    db_repo = container.preset_collaborators.db_repo
    if GenerationPolicy.is_admin(user):
        offered = {preset.preset_id for preset in db_repo.get_all_installed_presets()}
    else:
        offered = set(db_repo.get_available_preset_ids_for_user(user.id))
    return [_describe(template) for template in container.file_preset_repository.templates() if template.id in offered]


def _describe(template) -> PresetDescription:
    modes = {}
    for mode_name, mode in (template.modes or {}).items():
        forms = {}
        for form in mode.forms:
            described: Dict[str, FieldDescription] = {}
            _describe_fields(form.fields, described)
            forms[form.name] = described
        modes[mode_name] = ModeDescription(default_form=default_form_name(mode), forms=forms)
    return PresetDescription(
        id=template.id,
        name=template.name,
        vars=copy.deepcopy(template.vars or {}),
        modes=modes,
        engine=template.engine,
        category=template.category,
        requires=copy.deepcopy(template.requires),
    )
