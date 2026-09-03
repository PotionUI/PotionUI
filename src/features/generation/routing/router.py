"""`GenerationRouter` - see docs/generation-routing.md for the full picture
(flowchart, sequence diagram, decision table, worked examples)."""

import asyncio
import logging
import time
from typing import Any, List, Optional

from src.features.generation.routing.contracts import (
    Candidate,
    NoEligibleBackendError,
    RoutingContext,
    RoutingDecision,
    RoutingRequest,
    RoutingRule,
    RuleTraceEntry,
)

logger = logging.getLogger(__name__)


class GenerationRouter:
    """Decides which enabled backend of a preset's engine executes a
    generation, by folding an ordered chain of `RoutingRule`s over a
    candidate list (`rules.EnabledForEngine` seeds it; every rule after
    either drops a candidate, recording why, or annotates/leaves it
    untouched). The final pick among survivors is delegated to
    `BackendRegistry.select_backend_for_generation` (default backend, else
    highest priority) - one tested implementation of that tie-break, reused
    here rather than duplicated.

    Fast by construction: every built-in rule reads only in-memory state
    (the backend registry, the requirements cache's already-evaluated
    verdicts) - no network I/O, and no `await` that can block beyond a cache
    read. One pass over the chain, O(rules × candidates). A rule that needs
    real work done for a candidate with no verdict yet (a requirements
    check) schedules it via `schedule_background` and answers with what it
    already knows now - see `rules.RequirementsEligibility`.

    A `GenerationRouter` is a long-lived collaborator (one instance, built
    once in the composition root) - `route()` is called once per generation
    start.
    """

    def __init__(
        self,
        rules: List[RoutingRule],
        backend_registry: Any,
        requirements_cache: Optional[Any] = None,
        model_index: Optional[Any] = None,
        gpu_monitor: Optional[Any] = None,
    ):
        self._rules = rules
        self._backend_registry = backend_registry
        self._requirements_cache = requirements_cache
        self._model_index = model_index
        self._gpu_monitor = gpu_monitor
        # Keeps a rule's fire-and-forget background task (e.g. a cold-cache
        # requirements refresh) alive - same GC-safety reason as
        # `GenerationOrchestrator._bridge_tasks`/`_mesh_thumbnail_tasks`:
        # nothing else holds a reference to it, and a fire-and-forget task
        # with no referent is eligible for GC mid-flight.
        self._background_tasks: set = set()

    def _schedule_background(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _build_context(self) -> RoutingContext:
        return RoutingContext(
            backend_registry=self._backend_registry,
            requirements_cache=self._requirements_cache,
            model_index=self._model_index,
            gpu_monitor=self._gpu_monitor,
            schedule_background=self._schedule_background,
        )

    async def route(self, request: RoutingRequest) -> RoutingDecision:
        """Run the full rule chain for one `request` and return the
        resulting `RoutingDecision`.

        Raises:
            NoEligibleBackendError: every candidate was dropped somewhere in
                the chain - `decision.candidates` on the exception names why,
                per backend.
            NoBackendForEngineError: the engine has no enabled backend at
                all (raised by `rules.EnabledForEngine`'s empty seed reaching
                the final pick), or a `requested_backend_id` collides with
                `select_backend_for_generation`'s own pin check in a way
                `rules.RequestPin` didn't already catch - see
                `src.features.backends.backend_registry`.
            NoBackendHoldsAllModelsError: `rules.ModelAvailability` found
                model references no single candidate holds all of - the
                detailed, user-facing explanation propagates unchanged.
        """
        candidates: List[Candidate] = []
        trace: List[RuleTraceEntry] = []
        ctx = self._build_context()

        for rule in self._rules:
            before = sum(1 for c in candidates if not c.dropped)
            started = time.perf_counter()
            candidates = await rule.apply(candidates, request, ctx)
            elapsed_ms = (time.perf_counter() - started) * 1000
            after = sum(1 for c in candidates if not c.dropped)
            trace.append(RuleTraceEntry(rule=rule.name, before=before, after=after, ms=round(elapsed_ms, 3)))

        kept = [c for c in candidates if not c.dropped]
        for c in candidates:
            if c.dropped:
                logger.info(f"[ROUTER] dropped '{c.name}' ({c.backend_id}): {'; '.join(c.reasons)}")

        if not kept:
            raise NoEligibleBackendError(request.engine, RoutingDecision(chosen=None, candidates=candidates, rule_trace=trace))

        chosen = self._backend_registry.select_backend_for_generation(
            engine=request.engine,
            backend_id=request.requested_backend_id,
            allowed_backend_ids=[c.backend_id for c in kept],
        )

        decision = RoutingDecision(chosen=chosen, candidates=candidates, rule_trace=trace)
        total_ms = sum(t.ms for t in trace)
        logger.debug(
            f"[ROUTER] chose '{chosen.name}' ({chosen.backend_id}) for engine '{request.engine}' "
            f"in {total_ms:.2f}ms across {len(trace)} rule(s)"
        )
        return decision
