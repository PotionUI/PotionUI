"""Built-in routing rules, applied in this fixed order (`registry.BUILTIN_RULES`):

1. `EnabledForEngine` - seeds the candidate list.
2. `RequestPin` - a user-requested `backend_id` narrows to just that backend.
3. `ModelAvailability` - drops a backend that doesn't hold every selected model.
4. `RequirementsEligibility` - drops a backend with a cached hard-missing requirement.
5. `Preference` - annotates (never drops) which survivor is preferred.

See docs/generation-routing.md for the full picture (a flowchart of this
chain, a decision table per rule, and worked examples) - the docstring on
each rule below is the terse version.
"""

import logging
from typing import List, Set

from src.features.generation.routing.contracts import Candidate, RoutingContext, RoutingRequest

logger = logging.getLogger(__name__)

# Fires once per unindexed engine rather than on every generation submission -
# module-level like the pre-router `_warned_unindexed_engine` it replaces.
_warned_unindexed_engine: Set[str] = set()


class EnabledForEngine:
    """Seeds the candidate list from every enabled backend of the request's
    engine, highest priority first (`BackendRegistry.get_backends_for_engine`'s
    candidate set) - always first in the chain; every rule after this one
    only narrows or annotates what it produces. Never drops anything itself
    (an empty seed just means an empty candidate list for the next rule)."""

    name = "enabled_for_engine"

    async def apply(
        self, candidates: List[Candidate], request: RoutingRequest, ctx: RoutingContext
    ) -> List[Candidate]:
        backends = ctx.backend_registry.get_backends_for_engine(request.engine)
        return [Candidate(backend=b) for b in backends]


class RequestPin:
    """A `requested_backend_id` (a user pin, or a history re-run reproducing
    its original backend) that matches a live candidate wins immediately:
    every OTHER candidate is dropped, so later rules only ever validate the
    pin, never second-guess it in favor of a different backend. A pin that
    matches NO live candidate (wrong engine, disabled, never existed) drops
    everything, with a reason naming the pin - the router surfaces this as
    `NoEligibleBackendError` rather than silently falling back to some other
    backend the caller didn't ask for."""

    name = "request_pin"

    async def apply(
        self, candidates: List[Candidate], request: RoutingRequest, ctx: RoutingContext
    ) -> List[Candidate]:
        if not request.requested_backend_id:
            return candidates

        live = [c for c in candidates if not c.dropped]
        matched_ids = {c.backend_id for c in live if c.backend_id == request.requested_backend_id}

        if not matched_ids:
            for c in live:
                c.drop(
                    f"requested backend '{request.requested_backend_id}' is not an enabled "
                    f"'{request.engine}' backend"
                )
            return candidates

        for c in live:
            if c.backend_id in matched_ids:
                c.annotate(f"requested backend '{request.requested_backend_id}'")
            else:
                c.drop(f"not the requested backend '{request.requested_backend_id}'")
        return candidates


class ModelAvailability:
    """Drops a candidate that doesn't hold every `model:<id>` the submitted
    form references (`src.features.models.availability`). A no-op when the
    form carries no model references (legacy form data, or a preset whose
    model fields still store plain paths), or when no live candidate of this
    engine has been indexed yet - a configured-but-unindexed backend
    genuinely holds models, it has simply never been asked, and narrowing
    against an empty index would fail every generation on the engine rather
    than degrade to "don't narrow"."""

    name = "model_availability"

    async def apply(
        self, candidates: List[Candidate], request: RoutingRequest, ctx: RoutingContext
    ) -> List[Candidate]:
        from src.features.models.availability import require_candidate_backends
        from src.features.models.availability_repository import model_availability_repo
        from src.features.models.form_refs import collect_model_ids

        model_ids = collect_model_ids(request.form_data or {})
        if not model_ids:
            return candidates

        live = [c for c in candidates if not c.dropped]
        engine_backend_ids = [c.backend_id for c in live]
        if not model_availability_repo.any_indexed(engine_backend_ids):
            if request.engine not in _warned_unindexed_engine:
                logger.warning(
                    f"No backend for engine '{request.engine}' has been indexed; skipping "
                    f"availability narrowing. Index the backend to enable model-aware routing."
                )
                _warned_unindexed_engine.add(request.engine)
            return candidates

        # Not caught here: `NoBackendHoldsAllModelsError` carries a detailed,
        # user-facing explanation of which model blocks and where it lives -
        # it propagates through `route()` unchanged rather than being
        # flattened into a generic drop reason.
        allowed = set(require_candidate_backends(request.engine, model_ids, ctx.backend_registry))
        for c in live:
            if c.backend_id not in allowed:
                c.drop("does not hold every selected model")
        return candidates


class RequirementsEligibility:
    """Drops a candidate whose cached requirements verdict has a hard
    (non-optional) `missing` entry (docs/presets.md "Requirements"). A
    no-op when the preset declares no `requirements:` or no
    `requirements_cache` was wired. Reads the cache ONLY - never evaluates: a
    candidate with no cached verdict yet counts as "unknown", is kept, and
    gets a background refresh scheduled via `ctx.schedule_background` so a
    LATER generation benefits from a real one. A hard miss anywhere else
    (a host-scoped entry) never narrows here - see
    `src.features.presets.requirements.contracts.RequirementChecker`'s
    `scope` attribute; a host-scoped miss would exclude every backend
    identically, so it's the requirements panel's job to surface it, not
    routing's."""

    name = "requirements_eligibility"

    async def apply(
        self, candidates: List[Candidate], request: RoutingRequest, ctx: RoutingContext
    ) -> List[Candidate]:
        preset = request.preset
        if not getattr(preset, "requirements", None) or ctx.requirements_cache is None:
            return candidates

        from src.platform.plugins.requirement_checkers import requirement_checker_registry

        for c in [c for c in candidates if not c.dropped]:
            missing = ctx.requirements_cache.peek_backend_missing(
                requirement_checker_registry, preset, c.backend_id
            )
            if missing is None:
                c.annotate("requirements not yet checked")
                if ctx.schedule_background is not None:
                    ctx.schedule_background(_refresh_requirements(preset, c.backend, ctx))
                continue
            if missing:
                c.drop(f"missing requirement(s): {', '.join(missing)}")
            else:
                c.annotate("requirements satisfied")
        return candidates


async def _refresh_requirements(preset, backend, ctx: RoutingContext) -> None:
    """Background body for `RequirementsEligibility`'s cold-cache case - never
    awaited by the rule itself, never raises into its caller."""
    try:
        from src.features.presets.requirements.context_builder import (
            backend_infos_for_engine,
            build_requirement_context,
            build_requirement_context_for_backend,
        )
        from src.platform.plugins.requirement_checkers import requirement_checker_registry

        infos_by_id = {info.id: info for info in backend_infos_for_engine(ctx.backend_registry, backend.engine)}
        info = infos_by_id.get(backend.backend_id)
        if info is None:
            return

        host_ctx = build_requirement_context(preset, ctx.model_index, ctx.gpu_monitor, ctx.backend_registry)
        backend_ctx = build_requirement_context_for_backend(preset, ctx.model_index, ctx.gpu_monitor, info)
        await ctx.requirements_cache.get_or_evaluate_for_backends(
            requirement_checker_registry, preset, host_ctx, {info.id: backend_ctx},
        )
    except Exception:
        logger.debug(f"Background requirements refresh failed for backend {backend.backend_id}", exc_info=True)


class Preference:
    """Never drops - annotates which surviving candidate is the engine's
    default backend, or (absent a default) which has the highest `priority`,
    so the decision is self-explanatory. The actual pick among survivors
    happens once, in `BackendRegistry.select_backend_for_generation`
    (`GenerationRouter.route`'s last step) - this rule only explains it,
    rather than re-deciding it a second way."""

    name = "preference"

    async def apply(
        self, candidates: List[Candidate], request: RoutingRequest, ctx: RoutingContext
    ) -> List[Candidate]:
        live = [c for c in candidates if not c.dropped]
        if not live:
            return candidates

        default_config = ctx.backend_registry.backend_config_store.get_default_backend(request.engine)
        default_id = default_config.id if default_config is not None else None

        if default_id is not None and any(c.backend_id == default_id for c in live):
            for c in live:
                if c.backend_id == default_id:
                    c.annotate("default backend for this engine")
            return candidates

        highest = max(live, key=lambda c: c.backend.config.priority)
        highest.annotate(f"highest priority ({highest.backend.config.priority}) among eligible backends")
        return candidates
