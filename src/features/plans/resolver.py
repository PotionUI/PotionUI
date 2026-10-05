from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from src.features.plans.constants import (
    SOURCE_DEFAULT,
    SOURCE_GROUP,
    SOURCE_NONE,
    SOURCE_OVERRIDE,
    STEP_DEFAULT,
    STEP_GROUPS,
    STEP_OVERRIDE,
)
from src.features.plans.records import Plan, PlanSubject
from src.features.user_groups.constants import ALL_USERS_GROUP_ID


@dataclass(frozen=True)
class PlanCandidate:
    plan: Plan
    group_id: Optional[str] = None
    group_name: Optional[str] = None

    def group_ref(self) -> Optional[Dict[str, str]]:
        if self.group_id is None:
            return None
        return {"id": self.group_id, "name": self.group_name}


class PlanSource(Protocol):
    step: str
    source: str
    merge: bool

    def candidates(self, subject: PlanSubject, plans: Mapping[str, Plan]) -> List[PlanCandidate]: ...


class OverrideSource:
    step = STEP_OVERRIDE
    source = SOURCE_OVERRIDE
    merge = False

    def candidates(self, subject: PlanSubject, plans: Mapping[str, Plan]) -> List[PlanCandidate]:
        plan = plans.get(subject.override_plan_id or "")
        return [PlanCandidate(plan)] if plan else []


class GroupSource:
    step = STEP_GROUPS
    source = SOURCE_GROUP
    merge = True

    def candidates(self, subject: PlanSubject, plans: Mapping[str, Plan]) -> List[PlanCandidate]:
        found = []
        for group in subject.groups:
            if group.group_id == ALL_USERS_GROUP_ID:
                continue
            plan = plans.get(group.plan_id or "")
            if plan:
                found.append(PlanCandidate(plan, group.group_id, group.group_name))
        return found


class DefaultSource:
    step = STEP_DEFAULT
    source = SOURCE_DEFAULT
    merge = False

    def candidates(self, subject: PlanSubject, plans: Mapping[str, Plan]) -> List[PlanCandidate]:
        plan = plans.get(subject.default_plan_id or "")
        return [PlanCandidate(plan, ALL_USERS_GROUP_ID, "All users")] if plan else []


DEFAULT_SOURCES: Tuple[PlanSource, ...] = (OverrideSource(), GroupSource(), DefaultSource())


@dataclass(frozen=True)
class EffectiveLimit:
    kind: str
    value: Optional[float]
    candidate: Optional[PlanCandidate]
    source: str

    @property
    def plan(self) -> Optional[Plan]:
        return self.candidate.plan if self.candidate else None

    @property
    def limited(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class ResolutionStep:
    step: str
    candidates: Tuple[PlanCandidate, ...]


@dataclass(frozen=True)
class Resolution:
    subject: PlanSubject
    decided_by: str
    source: str
    steps: Tuple[ResolutionStep, ...]
    limits: Mapping[str, EffectiveLimit] = field(default_factory=dict)
    primary: Optional[PlanCandidate] = None
    mentioned: Tuple[str, ...] = ()

    def limited(self) -> List[EffectiveLimit]:
        return [limit for limit in self.limits.values() if limit.limited]

    def value(self, kind: str) -> Optional[float]:
        limit = self.limits.get(kind)
        return limit.value if limit else None

    def step(self, name: str) -> Tuple[PlanCandidate, ...]:
        for step in self.steps:
            if step.step == name:
                return step.candidates
        return ()


def _active_kinds(candidates: Sequence[PlanCandidate], is_active: Callable[[str], bool]) -> List[str]:
    kinds: List[str] = []
    for candidate in candidates:
        for limit in candidate.plan.limits:
            if limit.kind not in kinds and is_active(limit.kind):
                kinds.append(limit.kind)
    return kinds


def _most_generous(kind: str, candidates: Sequence[PlanCandidate], source: str) -> EffectiveLimit:
    best: Optional[EffectiveLimit] = None
    for candidate in candidates:
        if not candidate.plan.has(kind):
            return EffectiveLimit(kind, None, candidate, source)
        value = candidate.plan.value(kind)
        if best is None or value > best.value:
            best = EffectiveLimit(kind, value, candidate, source)
    return best


def _primary(candidates: Sequence[PlanCandidate], limits: Mapping[str, EffectiveLimit]) -> Optional[PlanCandidate]:
    if not candidates:
        return None
    wins = {id(candidate): 0 for candidate in candidates}
    for limit in limits.values():
        if limit.candidate is not None and id(limit.candidate) in wins:
            wins[id(limit.candidate)] += 1
    return max(candidates, key=lambda candidate: wins[id(candidate)])


class PlanResolver:

    def __init__(self, is_active: Callable[[str], bool], sources: Sequence[PlanSource] = DEFAULT_SOURCES):
        self.is_active = is_active
        self.sources = tuple(sources)

    def resolve(self, subject: PlanSubject, plans: Mapping[str, Plan]) -> Resolution:
        steps: List[ResolutionStep] = []
        decided: Optional[PlanSource] = None
        decided_candidates: List[PlanCandidate] = []
        mentioned: List[str] = []
        for source in self.sources:
            candidates = source.candidates(subject, plans)
            steps.append(ResolutionStep(source.step, tuple(candidates)))
            if decided is None and candidates:
                decided, decided_candidates = source, candidates
            for kind in _active_kinds(candidates, self.is_active):
                if kind not in mentioned:
                    mentioned.append(kind)
        if decided is None:
            return Resolution(subject, SOURCE_NONE, SOURCE_NONE, tuple(steps), {}, None, tuple(mentioned))
        kinds = _active_kinds(decided_candidates, self.is_active)
        if decided.merge:
            limits = {kind: _most_generous(kind, decided_candidates, decided.source) for kind in kinds}
        else:
            candidate = decided_candidates[0]
            limits = {kind: EffectiveLimit(kind, candidate.plan.value(kind), candidate, decided.source) for kind in kinds}
        return Resolution(
            subject=subject,
            decided_by=decided.step,
            source=decided.source,
            steps=tuple(steps),
            limits=limits,
            primary=_primary(decided_candidates, limits),
            mentioned=tuple(mentioned),
        )
