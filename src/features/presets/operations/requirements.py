"""Preset requirements operation: `GET /api/presets/{preset_id}/requirements`.

Module-level function, `PresetCollaborators` as its leading arg - same shape
as `src.features.presets.operations.query`.
"""

import time
from typing import Any, Dict, Optional

from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import PresetNotFoundException
from src.features.presets.requirements.context_builder import build_requirement_context
from src.features.presets.requirements.evaluator import evaluate_preset_requirements, summarize
from src.platform.plugins.requirement_checkers import (
    RequirementCheckerRegistration,
    requirement_checker_registry,
)


def _fallback_requirement_name(entry: Dict[str, Any]) -> str:
    """A requirement entry's most identifying string field, used when its
    checker either isn't registered or doesn't implement `describe()` (see
    `RequirementChecker`'s docstring)."""
    for key, value in entry.items():
        if key in ("type", "hint", "optional"):
            continue
        if isinstance(value, str) and value:
            return value
    return entry.get("type") or "requirement"


def _requirement_name(registration: Optional[RequirementCheckerRegistration], entry: Dict[str, Any]) -> str:
    if registration is not None:
        describe = getattr(registration.checker, "describe", None)
        if describe is not None:
            try:
                name = describe(entry)
            except Exception:
                name = None
            if name:
                return name
    return _fallback_requirement_name(entry)


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

    items = []
    for entry, result in zip(preset.requirements or [], results):
        registration = requirement_checker_registry.get(entry.get("type"))
        item = result.to_dict()
        item["type"] = entry.get("type")
        item["name"] = _requirement_name(registration, entry)
        item["optional"] = bool(entry.get("optional", False))
        items.append(item)

    return {
        "results": items,
        "summary": summarize(preset.requirements or [], results),
        "checked_at": checked_at,
    }
