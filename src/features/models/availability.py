"""
Which models can a preset actually use, and which backends could run it.

Selection would be circular if the picker were scoped to a backend: you would have to
choose a backend before choosing models, yet the backend is chosen from the models. So
the picker is populated from the **union** of models available on any enabled backend
whose engine matches the preset, and each entry carries the backends that hold it. Once
the user has chosen, `candidate_backends` narrows to the backends holding everything.

See docs/models.md.
"""

from typing import Dict, List, Optional, Set

from src.platform.observability.logger import logger
from src.features.models.availability_repository import (
    model_availability_repo,
)

# These warnings fire on every listing call for as long as a backend stays
# unenabled/unindexed - once per process is enough to surface the condition.
_warned_no_backend_for_engine: Set[str] = set()
_warned_unindexed_engine: Set[str] = set()


class NoBackendHoldsAllModelsError(RuntimeError):
    """No single backend of the preset's engine can load every selected model."""


def models_for_engine(
    engine: str,
    backend_registry,
    model_type: Optional[str] = None,
    search: Optional[str] = None,
    model_repository=None,
    admin: bool = False,
    user_allowed_model_ids: Optional[List[str]] = None,
    **list_kwargs,
) -> List[Dict]:
    """Every model loadable by at least one enabled backend of `engine`.

    Each entry gains `backend_ids` - the badge data that lets the UI show where a model
    lives, and grey out combinations no single backend can satisfy.

    Availability is pushed *into* the query as `allowed_model_ids`, not applied to its
    results. Post-filtering would force the call to be unpaginated (a LIMIT before the
    filter returns short pages), and `get_all` loads providers and tags per row - so the
    picker would fetch the entire library on every open.

    `user_allowed_model_ids` is a second, independent restriction:
    the caller's `ModelAccessPolicy.get_allowed_model_ids(user, all_models=True)` result
    - `None` for an admin (unrestricted), otherwise the user's own assigned model ids
    (STRICT: an empty list means the user sees nothing, not "unfiltered"). Intersected
    with the availability-derived `allowed_model_ids`, never replacing it - a model must
    be both available on this engine's backends AND visible to the user.
    """
    if model_repository is None:
        from src.features.models.repository import model_repo
        model_repository = model_repo

    backend_ids = sorted({b.backend_id for b in backend_registry.get_backends_for_engine(engine)})
    if not backend_ids:
        if engine not in _warned_no_backend_for_engine:
            logger.warning(f"[AVAILABILITY] No enabled backend provides engine '{engine}'")
            _warned_no_backend_for_engine.add(engine)
        return []

    # No backend of this engine has ever been indexed, so an empty availability table
    # means "nobody asked", not "nothing available". Constraining on it would leave the
    # picker blank. Show the index unbadged, exactly as before availability existed -
    # the same asymmetry the orchestrator applies when routing.
    indexed = model_availability_repo.any_indexed(backend_ids)
    if not indexed:
        if engine not in _warned_unindexed_engine:
            logger.warning(
                f"[AVAILABILITY] No '{engine}' backend has been indexed; listing all models "
                f"unfiltered. Index the backend to see what it can actually load."
            )
            _warned_unindexed_engine.add(engine)
        allowed_model_ids = None
    else:
        allowed_model_ids = model_availability_repo.model_ids_for_backends(backend_ids)
        if not allowed_model_ids:
            return []

    if user_allowed_model_ids is not None:
        if not user_allowed_model_ids:
            return []
        if allowed_model_ids is None:
            allowed_model_ids = list(user_allowed_model_ids)
        else:
            allowed_set = set(user_allowed_model_ids)
            allowed_model_ids = [m for m in allowed_model_ids if m in allowed_set]
            if not allowed_model_ids:
                return []

    models = model_repository.get_all(
        model_type=model_type,
        search=search,
        allowed_model_ids=allowed_model_ids,
        include_providers=list_kwargs.pop("include_providers", True),
        include_tags=list_kwargs.pop("include_tags", True),
        **list_kwargs,
    )

    if not indexed:
        return [_entry(model, [], admin) for model in models]

    # Badges only for the rows actually returned - a page, not the library.
    by_model = model_availability_repo.backend_ids_by_model([m.id for m in models])
    engine_backends = set(backend_ids)

    return [
        _entry(model, sorted(set(by_model.get(model.id, [])) & engine_backends), admin)
        for model in models
    ]


def _entry(model, backend_ids: List[str], admin: bool = False) -> Dict:
    if hasattr(model, "to_dict"):
        data = model.to_dict(admin=admin)
    else:
        data = dict(model.__dict__)

    # Which backends hold a model is operational detail. A generating user picks a model;
    # routing to a backend that can load it is the system's job, not theirs.
    if admin:
        data["backend_ids"] = backend_ids
    return data


def candidate_backends(
    engine: str,
    model_ids: List[str],
    backend_registry,
) -> List[str]:
    """Backends of `engine` that hold every one of `model_ids`, priority order preserved.

    An empty `model_ids` constrains nothing: a preset that selects no models can run on
    any backend of its engine.
    """
    ordered = [b.backend_id for b in backend_registry.get_backends_for_engine(engine)]
    if not model_ids:
        return ordered

    holders: Set[str] = model_availability_repo.backends_holding(model_ids)
    return [backend_id for backend_id in ordered if backend_id in holders]


def require_candidate_backends(
    engine: str,
    model_ids: List[str],
    backend_registry,
) -> List[str]:
    """As `candidate_backends`, but fail loudly rather than silently routing elsewhere."""
    candidates = candidate_backends(engine, model_ids, backend_registry)
    if candidates:
        return candidates

    missing = _explain_missing(engine, model_ids, backend_registry)
    raise NoBackendHoldsAllModelsError(
        f"No enabled '{engine}' backend can load every selected model. {missing}"
    )


def _explain_missing(engine: str, model_ids: List[str], backend_registry) -> str:
    """Name the models that no backend holds, so the error is actionable.

    `backends_holding`/`backend_ids_by_model` already exclude digest-conflicted rows
    from "holds" - a conflicted backend must not be selected - but that makes a
    conflict look identical to "never indexed" here unless it's checked for
    separately. A model whose only claim on this engine is conflicted gets its own
    message naming the backend and both digests, instead of the generic
    "not available anywhere", which would send an operator looking in the wrong
    place (re-download a model that is actually present, just mismatched).
    """
    from src.features.models.repository import model_repo

    engine_backend_ids = [b.backend_id for b in backend_registry.get_backends_for_engine(engine)]
    engine_backends = set(engine_backend_ids)
    by_model = model_availability_repo.backend_ids_by_model(model_ids)
    conflicts = model_availability_repo.conflicts_for(model_ids, engine_backend_ids)
    conflicts_by_model: Dict[str, List] = {}
    for row in conflicts:
        conflicts_by_model.setdefault(row.model_id, []).append(row)

    unheld = []
    conflict_messages = []
    for model_id in model_ids:
        if set(by_model.get(model_id, [])) & engine_backends:
            continue

        model = model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        name = model.filename if model else model_id

        rows = conflicts_by_model.get(model_id, [])
        if rows:
            for row in rows:
                expected = model.sha256 if model else None
                conflict_messages.append(
                    f"'{name}' on backend '{row.backend_id}': expected digest "
                    f"{(expected or '?')[:12]}…, found {(row.digest or '?')[:12]}…"
                )
        else:
            unheld.append(name)

    parts = []
    if unheld:
        parts.append(f"Not available on any backend: {', '.join(sorted(unheld))}.")
    if conflict_messages:
        parts.append(
            "Digest conflict - the backend's copy does not match the expected file: "
            + "; ".join(conflict_messages)
            + ". Re-sync the model file to that backend, or re-index it once fixed."
        )
    if parts:
        return " ".join(parts)

    return (
        "Each model is available somewhere, but no single backend holds all of them. "
        "Re-index the backends, or pick models that share one."
    )
