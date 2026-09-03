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

See docs/presets.md.
"""

from typing import List, Tuple

from src.features.generation.dto import GenerationRequest, PromptPair
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets import operations as preset_operations
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.linter import PresetLinter

__all__ = [
    "FilePresetRepository",
    "GenerationRequest",
    "PresetCollaborators",
    "lint_preset_dir",
    "preset_operations",
    "PromptPair",
]


def lint_preset_dir(path: str) -> Tuple[List[str], List[str]]:
    """Lint a single preset directory (containing a `preset.yml`).

    Returns `(errors, warnings)` as formatted strings, exactly like the issues
    `scripts/preset_lint.py` prints and `GET /api/developer/presets/lint`
    returns. Errors mean the preset will fail to load.
    """
    issues = PresetLinter([str(path)]).lint()
    errors = [str(issue) for issue in issues if issue.level == "error"]
    warnings = [str(issue) for issue in issues if issue.level == "warning"]
    return errors, warnings
