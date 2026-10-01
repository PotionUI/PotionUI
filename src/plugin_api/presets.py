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
    result = await orchestrator.start_generation(request, user.id)

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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.features.generation.dto import GenerationRequest, PromptPair
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets import operations as preset_operations
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.linter import PresetLinter
from src.features.presets.requirements.contracts import (
    RequirementAction,
    RequirementChecker,
    RequirementContext,
    RequirementResult,
)
from src.features.presets.schema import GalleryItem, PresetMedia
from src.features.presets.style_previews import downscale_and_save_webp
from src.features.presets.templates import default_form_name
from src.platform.plugins.runtime_registries import get_container

__all__ = [
    "FieldDescription",
    "FilePresetRepository",
    "GalleryItem",
    "GenerationRequest",
    "ModeDescription",
    "PresetCollaborators",
    "PresetDescription",
    "PresetMedia",
    "RequirementAction",
    "RequirementChecker",
    "RequirementContext",
    "RequirementResult",
    "describe_preset",
    "downscale_and_save_webp",
    "lint_preset_dir",
    "preset_operations",
    "PromptPair",
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


@dataclass(frozen=True)
class FieldDescription:
    type: Optional[str]
    required: bool


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


def _describe_fields(fields: Iterable[Any], out: Dict[str, FieldDescription]) -> None:
    for form_field in fields or []:
        if form_field.name and form_field.name not in out:
            out[form_field.name] = FieldDescription(type=form_field.type, required=bool(form_field.required))
        if isinstance(form_field.children, list):
            _describe_fields(form_field.children, out)


def describe_preset(preset_id: str) -> Optional[PresetDescription]:
    template = get_container().file_preset_repository.find_preset_by_id(preset_id)
    if template is None:
        return None
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
    )
