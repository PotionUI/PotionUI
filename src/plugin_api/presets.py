"""Presets and starting a generation.

`PresetManager` (over a `FilePresetRepository`) reads the installed presets, so a
plugin can find the preset it wants to run.

To actually generate, build a `GenerationRequest` - the preset, the mode, the
`PromptPair`s and the form data its fields expect - and hand it to the generation
orchestrator from the container:

    orchestrator = get_container().generation_orchestrator
    result = await orchestrator.start_generation(request, user.id)

See docs/presets.md.
"""

from src.features.generation.dto import GenerationRequest, PromptPair
from src.features.presets.manager import PresetManager
from src.features.presets.file_repository import FilePresetRepository

__all__ = [
    "FilePresetRepository",
    "GenerationRequest",
    "PresetManager",
    "PromptPair",
]
