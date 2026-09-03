"""The generation router's data model - see docs/generation-routing.md.

A `GenerationRouter` decides which enabled backend of a preset's `engine`
executes one generation by folding an ordered chain of `RoutingRule`s over a
candidate list: `Candidate` wraps one backend with a mutable trail of
`reasons` (why it was dropped, or why it's preferred) a rule can add to
without the router or any other rule needing to know what a given rule does
internally. Everything here is a plain, side-effect-free data holder except
`Candidate.drop`/`.annotate` (append-only list mutation) - a rule takes a
`RoutingContext` by value and never reaches into a container itself, same
discipline as `src.features.presets.requirements.contracts.RequirementContext`.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class RoutingRequest:
    """One generation's routing question: which backend of `engine` should
    run it. `preset` is the resolved `PresetTemplate` (untyped here to avoid
    a hard import - duck-typed on `.requirements`/`.id`/`.engine` by the
    rules that read it, same discipline as `RequirementContext.models`)."""

    engine: str
    preset: Any
    form_data: Dict[str, Any] = field(default_factory=dict)
    requested_backend_id: Optional[str] = None
    user_id: Optional[str] = None


@dataclass
class RoutingContext:
    """Everything a rule can read/use, gathered once per `GenerationRouter`
    (not per request - these are the router's own long-lived collaborators).
    `schedule_background` is how a rule fires a fire-and-forget task (e.g. a
    requirements refresh) without owning its own task-tracking set - the
    router owns that set (see `router.GenerationRouter`), the same GC-safety
    discipline `GenerationOrchestrator` already uses for its own background
    tasks."""

    backend_registry: Any
    requirements_cache: Optional[Any] = None
    model_index: Optional[Any] = None
    gpu_monitor: Optional[Any] = None
    schedule_background: Optional[Callable[[Coroutine], None]] = None


@dataclass
class Candidate:
    """One backend under consideration, plus why it is or isn't still in the
    running. `reasons` is append-only and never cleared by a later rule - the
    full trail is what makes a `RoutingDecision` self-explanatory without
    re-running anything."""

    backend: Any
    reasons: List[str] = field(default_factory=list)
    dropped: bool = False

    @property
    def backend_id(self) -> str:
        return self.backend.backend_id

    @property
    def name(self) -> str:
        return self.backend.name

    def drop(self, reason: str) -> None:
        """Remove this candidate from further consideration. Idempotent -
        dropping an already-dropped candidate just appends another reason
        (a later rule may have its own, independent objection)."""
        self.dropped = True
        self.reasons.append(reason)

    def annotate(self, reason: str) -> None:
        """Record something about this candidate without dropping it (a
        preference, a caveat, a "not yet checked") - purely informational,
        read by `RoutingDecision`/the trace, never by another rule."""
        self.reasons.append(reason)


@dataclass
class RuleTraceEntry:
    """One rule's contribution to a `RoutingDecision`: how many live
    (non-dropped) candidates existed before/after it ran, and how long it
    took. `before == after` for an annotate-only rule (`Preference`); a
    dropping rule (`ModelAvailability`, `RequirementsEligibility`,
    `RequestPin`) can shrink it."""

    rule: str
    before: int
    after: int
    ms: float


@dataclass
class RoutingDecision:
    """The outcome of `GenerationRouter.route()`: the chosen backend (never
    `None` when this is returned successfully - see `NoEligibleBackendError`
    for the failure case), every candidate considered (kept and dropped
    alike, each with its own reason trail), and the rule-by-rule trace."""

    chosen: Any
    candidates: List[Candidate]
    rule_trace: List[RuleTraceEntry]

    @property
    def kept(self) -> List[Candidate]:
        return [c for c in self.candidates if not c.dropped]

    @property
    def dropped(self) -> List[Candidate]:
        return [c for c in self.candidates if c.dropped]

    def summary(self) -> Dict[str, Any]:
        """A compact `{backend_id, backend_name, reason}` for a generation
        record/status field - "runs on X (default; requirements ok)". The
        one-line `reason` is the chosen candidate's last annotation (the most
        specific one a rule added), or `"selected"` if none was recorded."""
        chosen_candidate = next((c for c in self.candidates if c.backend is self.chosen), None)
        reason = chosen_candidate.reasons[-1] if chosen_candidate and chosen_candidate.reasons else "selected"
        return {"backend_id": self.chosen.backend_id, "backend_name": self.chosen.name, "reason": reason}

    def to_trace_dict(self) -> Dict[str, Any]:
        """The full decision as plain JSON-able data - see
        docs/generation-routing.md "Reading the trace"."""
        return {
            "chosen": {"backend_id": self.chosen.backend_id, "backend_name": self.chosen.name},
            "candidates": [
                {"backend_id": c.backend_id, "backend_name": c.name, "dropped": c.dropped, "reasons": list(c.reasons)}
                for c in self.candidates
            ],
            "rule_trace": [
                {"rule": t.rule, "before": t.before, "after": t.after, "ms": t.ms} for t in self.rule_trace
            ],
        }


class NoEligibleBackendError(RuntimeError):
    """Raised by `GenerationRouter.route()` when every candidate was dropped
    - the message renders each candidate's own drop reasons so the caller
    never has to re-derive "why" from a bare backend list."""

    def __init__(self, engine: str, decision: RoutingDecision):
        self.engine = engine
        self.decision = decision
        detail = "; ".join(
            f"'{c.name}': {', '.join(c.reasons) if c.reasons else 'excluded'}" for c in decision.candidates
        )
        message = f"No enabled backend for engine '{engine}' is eligible to run this generation"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


@runtime_checkable
class RoutingRule(Protocol):
    """One step of the router's chain. Pure by convention (no I/O beyond an
    in-memory cache read) - a rule that needs real work done (a live health
    check, say) schedules it via `ctx.schedule_background` and answers with
    what it already knows now, never blocking the chain on it. See
    `contracts` module docstring and docs/generation-routing.md's decision
    table for what each built-in rule does, why, and when."""

    # A short, stable, `snake_case` identifier - used as the `before:`/`after:`
    # anchor for a plugin-inserted rule (`registry.build_routing_rules`) and
    # as the `rule` field in a `RuleTraceEntry`.
    name: str

    async def apply(
        self, candidates: List[Candidate], request: RoutingRequest, ctx: RoutingContext
    ) -> List[Candidate]:
        """Narrow and/or annotate `candidates`, returning the (possibly
        shorter, never longer except for the seeding rule) list the next
        rule sees. The first rule in the chain (`rules.EnabledForEngine`) is
        the one exception: it receives an empty list and seeds it."""
        ...


__all__ = [
    "Candidate",
    "NoEligibleBackendError",
    "RoutingContext",
    "RoutingDecision",
    "RoutingRequest",
    "RoutingRule",
    "RuleTraceEntry",
]
