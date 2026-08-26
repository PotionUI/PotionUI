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

See docs/presets.md.
"""

from src.features.generation.dto import GenerationRequest, PromptPair
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets import operations as preset_operations
from src.features.presets.file_repository import FilePresetRepository

__all__ = [
    "FilePresetRepository",
    "GenerationRequest",
    "PresetCollaborators",
    "preset_operations",
    "PromptPair",
]
