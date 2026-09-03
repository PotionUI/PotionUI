"""Preset requirements operation: `GET /api/presets/{preset_id}/requirements`.

Module-level function, `PresetCollaborators` as its leading arg - same shape
as `src.features.presets.operations.query`.
"""

from typing import Any, Dict, List, Optional

from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import PresetNotFoundException
from src.features.presets.requirements.context_builder import (
    backend_infos_for_engine,
    build_requirement_context,
    build_requirement_context_for_backend,
    resolve_backend_id,
)
from src.features.presets.requirements.contracts import RequirementBackendInfo
from src.features.presets.requirements.evaluator import (
    MultiBackendRequirementResults,
    describe_requirement_entry,
    evaluate_preset_requirements_for_backends,
)
from src.platform.plugins.requirement_checkers import requirement_checker_registry


def _resolve_chosen_backend(
    requested_id: Optional[str],
    default_id: Optional[str],
    backend_infos: List[RequirementBackendInfo],
    evaluation: MultiBackendRequirementResults,
) -> Optional[str]:
    """Which backend's results populate `results`/`summary`: the requested
    `?backend_id=`, else the engine's default backend, else - when neither is
    a candidate - the best-scoring enabled backend (fewest hard misses, then
    fewest unknowns). `None` when the engine has no enabled backend at all -
    `results`/`summary` then carry the host-scoped entries only."""
    candidate_ids = {info.id for info in backend_infos}
    if requested_id and requested_id in candidate_ids:
        return requested_id
    if default_id and default_id in candidate_ids:
        return default_id
    if not backend_infos:
        return None
    return min(
        backend_infos,
        key=lambda info: (
            evaluation.summary_for(info.id).get("missing", 0),
            evaluation.summary_for(info.id).get("unknown", 0),
        ),
    ).id


def _build_result_items(
    preset_requirements: List[Dict[str, Any]],
    evaluation: MultiBackendRequirementResults,
    chosen_id: Optional[str],
) -> List[Dict[str, Any]]:
    full_results = evaluation.full_results(chosen_id)
    backend_scoped = set(evaluation.backend_indices)
    items = []
    for i, (entry, result) in enumerate(zip(preset_requirements, full_results)):
        item = result.to_dict()
        item["type"] = entry.get("type")
        item["name"] = describe_requirement_entry(requirement_checker_registry, entry)
        item["optional"] = bool(entry.get("optional", False))
        if chosen_id is not None and i in backend_scoped:
            item["backend_id"] = chosen_id
        items.append(item)
    return items


async def get_preset_requirements(
    collaborators: PresetCollaborators,
    preset_id: str,
    backend_id: Optional[str] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Check a preset's declared `requirements:` against this instance.

    A "host"-scoped entry (see `contracts.RequirementChecker`'s `scope`
    attribute) is evaluated once for the whole preset; a "backend"-scoped
    entry (e.g. a ComfyUI custom node) is evaluated once per enabled backend
    of the preset's engine. `results`/`summary` reflect the host entries plus
    one CHOSEN backend's - the requested `backend_id` if it's a candidate,
    else the engine's default backend, else the best-scoring one -
    `backends` lists every candidate with its own summary so a UI can offer a
    selector. Cached per (preset id, requirements-block fingerprint[, backend
    id]) - pass `?refresh=1` to force a fresh evaluation.

    Raises:
        PresetNotFoundException: If the preset is not found
    """
    preset = collaborators.file_repo.find_preset_by_id(preset_id)
    if not preset:
        raise PresetNotFoundException(preset_id)

    host_ctx = build_requirement_context(
        preset, collaborators.model_index, collaborators.gpu_monitor, collaborators.backend_registry
    )
    backend_infos = backend_infos_for_engine(collaborators.backend_registry, preset.engine)
    backend_ctxs = {
        info.id: build_requirement_context_for_backend(
            preset, collaborators.model_index, collaborators.gpu_monitor, info
        )
        for info in backend_infos
    }

    if collaborators.requirements_cache is not None:
        evaluation = await collaborators.requirements_cache.get_or_evaluate_for_backends(
            requirement_checker_registry, preset, host_ctx, backend_ctxs, refresh=refresh,
        )
    else:
        evaluation = await evaluate_preset_requirements_for_backends(
            requirement_checker_registry, preset, host_ctx, backend_ctxs
        )

    default_backend_id = resolve_backend_id(collaborators.backend_registry, preset.engine)
    chosen_id = _resolve_chosen_backend(backend_id, default_backend_id, backend_infos, evaluation)

    preset_requirements = preset.requirements or []
    items = _build_result_items(preset_requirements, evaluation, chosen_id)

    backends_payload = [
        {
            "id": info.id,
            "name": info.name or info.id,
            "is_default": info.id == default_backend_id,
            "summary": evaluation.summary_for(info.id),
        }
        for info in backend_infos
    ]

    return {
        "results": items,
        "summary": evaluation.summary_for(chosen_id),
        "backends": backends_payload,
        "checked_at": evaluation.checked_at_for(chosen_id),
    }
