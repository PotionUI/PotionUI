import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from src.platform.database.rows import dt_column, dt_iso


@dataclass(frozen=True)
class PlanLimit:
    kind: str
    value: float


def parse_limits(raw: Optional[str]) -> Tuple[PlanLimit, ...]:
    try:
        entries = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return ()
    limits = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("kind"), str):
            continue
        value = entry.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            limits.append(PlanLimit(kind=entry["kind"], value=value))
    return tuple(limits)


def dump_limits(limits: Tuple[PlanLimit, ...]) -> str:
    return json.dumps([{"kind": limit.kind, "value": limit.value} for limit in limits])


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    description: Optional[str] = None
    limits: Tuple[PlanLimit, ...] = field(default_factory=tuple)
    is_system: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def value(self, kind: str) -> Optional[float]:
        for limit in self.limits:
            if limit.kind == kind:
                return limit.value
        return None

    def has(self, kind: str) -> bool:
        return any(limit.kind == kind for limit in self.limits)

    def ref(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name}

    def timestamps(self) -> Dict[str, Optional[str]]:
        return {"created_at": dt_iso(self.created_at), "updated_at": dt_iso(self.updated_at)}

    @classmethod
    def from_row(cls, row) -> "Plan":
        return cls(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            limits=parse_limits(row["limits_json"]),
            is_system=bool(row["is_system"]),
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
        )


@dataclass(frozen=True)
class GroupPlan:
    group_id: str
    group_name: str
    plan_id: Optional[str]


@dataclass(frozen=True)
class PlanSubject:
    user_id: str
    username: str
    email: str
    account_type: str
    override_plan_id: Optional[str]
    groups: Tuple[GroupPlan, ...]
    default_plan_id: Optional[str]

    @property
    def is_admin(self) -> bool:
        return self.account_type == "ADMIN"

    def with_group_plan(self, group_id: str, plan_id: Optional[str], all_users_group_id: str) -> "PlanSubject":
        groups = tuple(
            GroupPlan(g.group_id, g.group_name, plan_id) if g.group_id == group_id else g for g in self.groups
        )
        default = plan_id if group_id == all_users_group_id else self.default_plan_id
        return PlanSubject(self.user_id, self.username, self.email, self.account_type,
                           self.override_plan_id, groups, default)
