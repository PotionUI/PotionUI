"""Preset requirements operation: `GET /api/presets/{preset_id}/requirements`.

Module-level function, `PresetCollaborators` as its leading arg - same shape
as `src.features.presets.operations.query`.
"""

import time
from typing import Any, Dict

from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import PresetNotFoundException
from src.features.presets.requirements.context_builder import build_requirement_context
from src.features.presets.requirements.evaluator import evaluate_preset_requirements, summarize
from src.platform.plugins.requirement_checkers import requirement_checker_registry


async def get_preset_requirements(
    collaborators: PresetCollaborators, preset_id: str, refresh: bool = False
) -> Dict[str, Any]:
    """Check a preset's declared `requirements:` against this instance.

    Cached per (preset id, requirements-block fingerprint, resolved default
    backend for the preset's engine) - pass `refresh=True` to force a fresh
    evaluation. See docs/presets.md "Requirements".

    Raises:
        PresetNotFoundException: If the preset is not found
    """
    preset = collaborators.file_repo.find_preset_by_id(preset_id)
    if not preset:
        raise PresetNotFoundException(preset_id)

    ctx = build_requirement_context(
        preset, collaborators.model_index, collaborators.gpu_monitor, collaborators.backend_registry
    )
    backend_id = ctx.backend.id if ctx.backend is not None else None

    if collaborators.requirements_cache is not None:
        results, checked_at = await collaborators.requirements_cache.get_or_evaluate(
            requirement_checker_registry, preset, ctx, backend_id=backend_id, refresh=refresh,
        )
    else:
        results = await evaluate_preset_requirements(requirement_checker_registry, preset, ctx)
        checked_at = time.time()

    return {
        "results": [r.to_dict() for r in results],
        "summary": summarize(results),
        "checked_at": checked_at,
    }
