import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from src.features.plans.constants import (
    MAX_CONTACT_LINE,
    MAX_PLAN_DESCRIPTION,
    MAX_PLAN_NAME,
    SETTING_CONTACT_LINE,
    SETTING_DAY_TIMEZONE,
    SETTING_EXEMPT_ADMINS,
    SOURCE_DEFAULT,
    SOURCE_GROUP,
    SOURCE_OVERRIDE,
    STEP_DEFAULT,
    STEP_GROUPS,
    STEP_OVERRIDE,
)
from src.features.plans.errors import PlanError
from src.features.plans.guard import LimitGuard, Measured, PlanSettings
from src.features.plans.hooks import PLANS_HOOKS
from src.features.plans.kinds import describe, format_amount
from src.features.plans.records import Plan, PlanLimit, PlanSubject
from src.features.plans.resolver import EffectiveLimit, Resolution
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.plugins.limit_kinds import LimitKind

logger = logging.getLogger(__name__)

_MAX_PAGE = 500


def _number(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def _not_found(what: str) -> PlanError:
    return PlanError(f"{what}_not_found", f"{what.capitalize()} not found", 404)


@dataclass
class Person:
    subject: PlanSubject
    resolution: Resolution
    exempt: bool
    measured: Dict[str, Measured] = field(default_factory=dict)


class PlansManager:

    def __init__(self, guard: LimitGuard, hook_runner: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self.guard = guard
        self.registry = guard.registry
        self.plans = guard.plans
        self.usage = guard.usage
        self.settings_store = guard.settings
        self.hook_runner = hook_runner

    def kinds(self) -> Dict[str, Any]:
        tz_name = self.settings_store.read().day_timezone
        return {"kinds": [describe(kind, tz_name) for kind in self.registry.all()]}

    def _measure(self, person: Person, kind_key: str, settings: PlanSettings) -> Optional[Measured]:
        if kind_key in person.measured:
            return person.measured[kind_key]
        kind = self.registry.get(kind_key)
        if kind is None:
            return None
        try:
            measured = self.guard.measure(kind, person.subject.user_id, settings.day_timezone)
        except Exception:
            logger.exception("Limit kind %s failed to measure for a usage view", kind_key)
            measured = Measured(used=0.0, window_start=None, resets_at=None)
        person.measured[kind_key] = measured
        return measured

    def _people(self, subjects: List[PlanSubject], plans: Mapping[str, Plan], settings: PlanSettings) -> List[Person]:
        return [
            Person(subject, self.guard.resolve(subject, plans), self.guard.is_exempt(subject, settings))
            for subject in subjects
        ]

    def _row(self, person: Person, limit: EffectiveLimit, settings: PlanSettings, admin_view: bool) -> Optional[Dict[str, Any]]:
        measured = self._measure(person, limit.kind, settings)
        if measured is None:
            return None
        return self.guard.usage_row(limit, measured, admin_view=admin_view, enforced=not person.exempt)

    def _effective(self, person: Person, kind: str) -> EffectiveLimit:
        return person.resolution.limits.get(kind) or EffectiveLimit(kind, None, None, person.resolution.source)

    def _header(self, resolution: Resolution) -> Dict[str, Any]:
        primary = resolution.primary
        return {
            "plan": primary.plan.ref() if primary else None,
            "source": resolution.source,
            "group": primary.group_ref() if primary and resolution.source == SOURCE_GROUP else None,
        }

    def my_limits(self, user) -> Dict[str, Any]:
        settings = self.settings_store.read()
        subject = self.plans.subject(user.id)
        base = {
            "plan": None, "source": "none", "group": None, "exempt": False,
            "timezone": settings.day_timezone, "contact_line": settings.contact_line,
            "payments": False, "limits": [],
        }
        if subject is None:
            return base
        person = self._people([subject], self.guard.plans_by_id(), settings)[0]
        rows = [self._row(person, limit, settings, admin_view=False) for limit in person.resolution.limited()]
        return {
            **base,
            **self._header(person.resolution),
            "exempt": person.exempt,
            "limits": [
                {**row, "kind_info": describe(self.registry.get(row["kind"]), settings.day_timezone)}
                for row in rows if row is not None
            ],
        }

    def my_storage(self, user) -> Dict[str, Any]:
        groups = self.usage.storage_breakdown(user.id)
        return {"total_bytes": sum(group["bytes"] for group in groups), "groups": groups}

    def _validate_plan(self, body, existing: Optional[Plan]) -> Tuple[str, Optional[str], Tuple[PlanLimit, ...]]:
        problems: List[Dict[str, Any]] = []
        name = (body.name or "").strip()
        if not name:
            problems.append({"code": "name_required", "message": "A plan needs a name"})
        elif len(name) > MAX_PLAN_NAME:
            problems.append({"code": "name_too_long", "message": f"Keep the name under {MAX_PLAN_NAME} characters"})
        description = (body.description or "").strip() or None
        if description and len(description) > MAX_PLAN_DESCRIPTION:
            problems.append({"code": "description_too_long", "message": f"Keep the description under {MAX_PLAN_DESCRIPTION} characters"})
        stored = {limit.kind for limit in existing.limits} if existing else set()
        seen = set()
        limits = []
        for entry in body.limits:
            kind_key = entry.get("kind") if isinstance(entry, dict) else None
            value = entry.get("value") if isinstance(entry, dict) else None
            if not isinstance(kind_key, str) or not kind_key:
                problems.append({"code": "unknown_kind", "kind": kind_key, "message": "Each limit needs a kind"})
                continue
            kind = self.registry.get(kind_key)
            if kind is None and kind_key not in stored:
                problems.append({"code": "unknown_kind", "kind": kind_key, "message": f"Unknown limit kind '{kind_key}'"})
                continue
            if kind_key in seen:
                problems.append({"code": "duplicate_kind", "kind": kind_key, "message": "A kind appears at most once per plan"})
                continue
            seen.add(kind_key)
            whole = kind is not None and kind.value_type in ("bytes", "count")
            if (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
                or (whole and not float(value).is_integer())
            ):
                problems.append({
                    "code": "invalid_value", "kind": kind_key,
                    "message": "Use a whole number of 0 or more" if whole else "Use a number of 0 or more",
                })
                continue
            limits.append(PlanLimit(kind=kind_key, value=int(value) if whole else value))
        if problems:
            raise PlanError("invalid_plan", "The plan is not valid", 422, problems=problems)
        if self.plans.name_taken(name, existing.id if existing else None):
            raise PlanError("plan_name_taken", "Another plan already has this name", 409)
        return name, description, tuple(limits)

    def _plan_dict(self, plan: Plan, counts: Mapping[str, Mapping[str, int]], default_id: Optional[str]) -> Dict[str, Any]:
        assigned = counts.get(plan.id, {})
        return {
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "is_system": plan.is_system,
            "is_default": plan.id == default_id,
            "limits": [
                {"kind": limit.kind, "value": _number(limit.value), "active": self.registry.get(limit.kind) is not None}
                for limit in plan.limits
            ],
            "assigned": {"groups": assigned.get("groups", 0), "users": assigned.get("users", 0)},
            **plan.timestamps(),
        }

    def _plans_in_effect(self, person: Person) -> Dict[str, Plan]:
        found: Dict[str, Plan] = {}
        if person.resolution.primary is not None:
            found[person.resolution.primary.plan.id] = person.resolution.primary.plan
        for limit in person.resolution.limits.values():
            if limit.plan is not None:
                found[limit.plan.id] = limit.plan
        return found

    def _population(self, settings: PlanSettings, plans: Mapping[str, Plan]) -> List[Person]:
        return self._people(self.plans.subjects(), plans, settings)

    def _plan_usage(self, plan: Plan, people: List[Person], settings: PlanSettings) -> Dict[str, Any]:
        members = [person for person in people if plan.id in self._plans_in_effect(person)]
        kinds = []
        above_warn = 0
        at_limit = 0
        for person in members:
            warned = full = False
            for limit in plan.limits:
                kind = self.registry.get(limit.kind)
                if kind is None:
                    continue
                measured = self._measure(person, limit.kind, settings)
                ratio = 1.0 if limit.value <= 0 else measured.used / limit.value
                warned = warned or ratio >= kind.warn_at
                full = full or ratio >= 1
            above_warn += int(warned)
            at_limit += int(full)
        for limit in plan.limits:
            if self.registry.get(limit.kind) is None:
                continue
            used = sum(self._measure(person, limit.kind, settings).used for person in members)
            kinds.append({
                "kind": limit.kind,
                "used_total": _number(used),
                "limit_total": _number(limit.value * len(members)),
            })
        return {"people": len(members), "above_warn": above_warn, "at_limit": at_limit, "kinds": kinds}

    def list_plans(self) -> Dict[str, Any]:
        settings = self.settings_store.read()
        plans = self.plans.list_plans()
        by_id = {plan.id: plan for plan in plans}
        default_id = self.plans.default_plan_id()
        counts = self.plans.assignment_counts()
        people = self._population(settings, by_id)
        entries = []
        for plan in plans:
            usage = self._plan_usage(plan, people, settings)
            entry = self._plan_dict(plan, counts, default_id)
            entry["usage"] = {
                "members": usage["people"],
                "kinds": [{"kind": k["kind"], "used_total": k["used_total"]} for k in usage["kinds"]],
            }
            entries.append(entry)
        entries.sort(key=lambda e: (not e["is_default"], e["is_system"], e["name"].casefold()))
        return {
            "plans": entries,
            "kinds": self.kinds()["kinds"],
            "settings": self.get_settings(),
            "total": len(entries),
        }

    def _require_plan(self, plan_id: str) -> Plan:
        plan = self.plans.get(plan_id)
        if plan is None:
            raise _not_found("plan")
        return plan

    def get_plan(self, plan_id: str) -> Dict[str, Any]:
        plan = self._require_plan(plan_id)
        settings = self.settings_store.read()
        by_id = self.guard.plans_by_id()
        default_id = self.plans.default_plan_id()
        groups = [
            {"id": g["id"], "name": g["name"], "members": g["members"], "is_default": g["id"] == ALL_USERS_GROUP_ID}
            for g in self.plans.groups() if g["plan_id"] == plan.id
        ]
        users = self.plans.users_with_plan(plan.id)
        people = self._population(settings, by_id)
        return {
            "plan": self._plan_dict(plan, self.plans.assignment_counts(), default_id),
            "assigned_to": {"groups": groups, "users": [{"id": u["id"], "username": u["username"]} for u in users]},
            "in_use": self._plan_usage(plan, people, settings),
        }

    def create_plan(self, body) -> Dict[str, Any]:
        name, description, limits = self._validate_plan(body, None)
        plan = self.plans.create(name, description, limits)
        return self._plan_dict(plan, {}, self.plans.default_plan_id())

    def update_plan(self, plan_id: str, body) -> Dict[str, Any]:
        plan = self._require_plan(plan_id)
        if plan.is_system:
            raise PlanError("plan_is_system", "The built-in plan cannot be changed", 409)
        name, description, limits = self._validate_plan(body, plan)
        updated = self.plans.update(plan.id, name, description, limits)
        return self._plan_dict(updated, self.plans.assignment_counts(), self.plans.default_plan_id())

    def delete_plan(self, plan_id: str, reassign_to: Optional[str]) -> Dict[str, Any]:
        plan = self._require_plan(plan_id)
        if plan.is_system:
            raise PlanError("plan_is_system", "The built-in plan cannot be deleted", 409)
        groups = [{"id": g["id"], "name": g["name"]} for g in self.plans.groups() if g["plan_id"] == plan.id]
        users = [{"id": u["id"], "username": u["username"]} for u in self.plans.users_with_plan(plan.id)]
        target: Optional[str] = None
        if reassign_to is None:
            if groups or users:
                raise PlanError(
                    "plan_in_use", "This plan is still assigned. Reassign it first.", 409,
                    assigned_to={"groups": groups, "users": users},
                )
        elif reassign_to != "none":
            if reassign_to == plan.id:
                raise PlanError("invalid_reassign", "Pick a different plan to reassign to", 422)
            target = self._require_plan(reassign_to).id
        moved = self.plans.delete(plan.id, target)
        return {"deleted": True, "reassigned": moved}

    def get_settings(self) -> Dict[str, Any]:
        settings = self.settings_store.read()
        return {
            "default_plan_id": self.plans.default_plan_id(),
            "exempt_admins": settings.exempt_admins,
            "day_timezone": settings.day_timezone,
            "contact_line": settings.contact_line,
        }

    def update_settings(self, body, actor_id: str) -> Dict[str, Any]:
        from src.features.plans.windows import valid_timezone

        fields = body.provided()
        problems = []
        if "exempt_admins" in fields and not isinstance(fields["exempt_admins"], bool):
            problems.append({"field": "exempt_admins", "message": "Use true or false"})
        if "day_timezone" in fields and not valid_timezone(fields["day_timezone"] or ""):
            problems.append({"field": "day_timezone", "message": "Use a timezone name such as UTC or Europe/Warsaw"})
        if "contact_line" in fields:
            line = fields["contact_line"]
            if line is not None and len(line) > MAX_CONTACT_LINE:
                problems.append({"field": "contact_line", "message": f"Keep it under {MAX_CONTACT_LINE} characters"})
        if problems:
            raise PlanError("invalid_settings", "The plan settings are not valid", 422, problems=problems)
        if "default_plan_id" in fields:
            self.assign_group(ALL_USERS_GROUP_ID, fields["default_plan_id"], actor_id)
        if "exempt_admins" in fields:
            self.settings_store.write(SETTING_EXEMPT_ADMINS, fields["exempt_admins"])
        if "day_timezone" in fields:
            self.settings_store.write(SETTING_DAY_TIMEZONE, fields["day_timezone"])
        if "contact_line" in fields:
            self.settings_store.write(SETTING_CONTACT_LINE, (fields["contact_line"] or "").strip())
        return self.get_settings()

    def _fire_assignment(self, target: str, target_id: str, plan_id: Optional[str], actor_id: str) -> None:
        if self.hook_runner is None:
            return
        try:
            self.hook_runner(PLANS_HOOKS.assignment_changed, {
                "target": target, "target_id": target_id, "plan_id": plan_id, "actor_id": actor_id,
            })
        except Exception:
            logger.exception("plans.assignment_changed hook failed")

    def list_groups(self) -> Dict[str, Any]:
        by_id = self.guard.plans_by_id()
        return {"groups": [
            {
                "id": g["id"], "name": g["name"], "is_system": bool(g["is_system"]), "members": g["members"],
                "plan": by_id[g["plan_id"]].ref() if g["plan_id"] in by_id else None,
            }
            for g in self.plans.groups()
        ]}

    def assign_group(self, group_id: str, plan_id: Optional[str], actor_id: str) -> Dict[str, Any]:
        group = self.plans.group(group_id)
        if group is None:
            raise _not_found("group")
        plan = self._require_plan(plan_id) if plan_id else None
        self.plans.set_group_plan(group_id, plan.id if plan else None)
        self._fire_assignment("group", group_id, plan.id if plan else None, actor_id)
        return {"group": {"id": group["id"], "name": group["name"]}, "plan": plan.ref() if plan else None}

    def _value_ref(self, limit: Optional[EffectiveLimit]) -> Dict[str, Any]:
        if limit is None:
            return {"value": None, "source": "none", "plan": None, "group": None}
        candidate = limit.candidate
        return {
            "value": _number(limit.value),
            "source": limit.source,
            "plan": candidate.plan.ref() if candidate else None,
            "group": candidate.group_ref() if candidate else None,
        }

    def impact(self, group_id: str, plan_id: Optional[str]) -> Dict[str, Any]:
        group = self.plans.group(group_id)
        if group is None:
            raise _not_found("group")
        proposed = self._require_plan(plan_id) if plan_id else None
        settings = self.settings_store.read()
        plans = self.guard.plans_by_id()
        current = plans.get(group["plan_id"] or "")
        if group_id == ALL_USERS_GROUP_ID:
            subjects = self.plans.subjects()
        else:
            subjects = self.plans.subjects(self.plans.group_member_ids(group_id))
        members = []
        kinds: List[str] = []
        changed_people = 0
        over_people = 0
        for subject in subjects:
            person = Person(subject, self.guard.resolve(subject, plans), self.guard.is_exempt(subject, settings))
            after = self.guard.resolve(
                subject.with_group_plan(group_id, proposed.id if proposed else None, ALL_USERS_GROUP_ID), plans
            )
            keys = list(dict.fromkeys(list(person.resolution.limits) + list(after.limits)))
            rows = []
            changed = over = False
            for key in keys:
                if key not in kinds:
                    kinds.append(key)
                before_limit = person.resolution.limits.get(key)
                after_limit = after.limits.get(key)
                before_value = before_limit.value if before_limit else None
                after_value = after_limit.value if after_limit else None
                change = self._change(before_value, after_value)
                measured = self._measure(person, key, settings)
                used = measured.used if measured else 0.0
                over_after = after_value is not None and used >= after_value and not person.exempt
                changed = changed or change != "unchanged"
                over = over or over_after
                rows.append({
                    "kind": key,
                    "before": self._value_ref(before_limit),
                    "after": self._value_ref(after_limit),
                    "change": change,
                    "note": self._note(person, after, after_limit, group_id),
                    "used": _number(used),
                    "over_after": over_after,
                })
            changed_people += int(changed)
            over_people += int(over)
            members.append({
                "user_id": subject.user_id,
                "username": subject.username,
                "exempt": person.exempt,
                "override": after.decided_by == STEP_OVERRIDE,
                "limits": rows,
            })
        return {
            "group": {"id": group["id"], "name": group["name"]},
            "current_plan": current.ref() if current else None,
            "proposed_plan": proposed.ref() if proposed else None,
            "kinds": [describe(self.registry.get(k), settings.day_timezone) for k in kinds if self.registry.get(k)],
            "members": members,
            "summary": {"members": len(members), "changed": changed_people, "over_after": over_people},
        }

    @staticmethod
    def _change(before: Optional[float], after: Optional[float]) -> str:
        if before is None and after is None:
            return "unchanged"
        if before is None:
            return "added"
        if after is None:
            return "lifted"
        if after > before:
            return "raised"
        if after < before:
            return "lowered"
        return "unchanged"

    @staticmethod
    def _note(person: Person, after: Resolution, limit: Optional[EffectiveLimit], group_id: str) -> str:
        if person.exempt:
            return "exempt"
        if after.decided_by == STEP_OVERRIDE:
            return "override"
        if limit is None or limit.candidate is None:
            return "none"
        if limit.candidate.group_id == group_id:
            return "from_this_group"
        if limit.source == SOURCE_GROUP:
            return "kept_from_other_group"
        if limit.source == SOURCE_DEFAULT:
            return "default"
        return "none"

    def _require_subject(self, user_id: str) -> PlanSubject:
        subject = self.plans.subject(user_id)
        if subject is None:
            raise _not_found("user")
        return subject

    def assign_user(self, user_id: str, plan_id: Optional[str], actor_id: str) -> Dict[str, Any]:
        self._require_subject(user_id)
        plan = self._require_plan(plan_id) if plan_id else None
        self.plans.set_user_plan(user_id, plan.id if plan else None)
        self._fire_assignment("user", user_id, plan.id if plan else None, actor_id)
        return self.user_detail(user_id)

    def _detail_line(self, kind: LimitKind, limit: EffectiveLimit, resolution: Resolution) -> str:
        candidate = limit.candidate
        if candidate is None:
            return "No plan limits this"
        amount = "Unlimited" if limit.value is None else format_amount(kind, limit.value)
        if limit.source == SOURCE_OVERRIDE:
            line = f"{candidate.plan.name} (personal override): {amount}"
        elif limit.source == SOURCE_GROUP:
            line = f"{candidate.plan.name} via {candidate.group_name}"
        else:
            line = f"{candidate.plan.name} (default plan)"
        defaults = resolution.step(STEP_DEFAULT)
        if limit.source != SOURCE_DEFAULT and defaults and defaults[0].plan.has(kind.key):
            default_value = defaults[0].plan.value(kind.key)
            if limit.value is None or default_value < limit.value:
                comparison = "smaller"
            elif default_value > limit.value:
                comparison = "larger"
            else:
                comparison = "the same"
            line += f"; All users says {format_amount(kind, default_value)}, {comparison}"
        return line

    def user_detail(self, user_id: str) -> Dict[str, Any]:
        subject = self._require_subject(user_id)
        settings = self.settings_store.read()
        plans = self.guard.plans_by_id()
        person = self._people([subject], plans, settings)[0]
        resolution = person.resolution
        rows = []
        for key in resolution.mentioned:
            kind = self.registry.get(key)
            limit = self._effective(person, key)
            row = self._row(person, limit, settings, admin_view=True)
            if kind is None or row is None:
                continue
            row["detail"] = self._detail_line(kind, limit, resolution)
            rows.append(row)
        override = plans.get(subject.override_plan_id or "")
        steps = []
        for step in resolution.steps:
            if step.step == STEP_GROUPS:
                steps.append({"step": step.step, "plans": [
                    {"plan": c.plan.ref(), "group": c.group_ref()} for c in step.candidates
                ]})
            else:
                steps.append({"step": step.step, "plan": step.candidates[0].plan.ref() if step.candidates else None})
        return {
            "user": {"id": subject.user_id, "username": subject.username, "account_type": subject.account_type},
            "override_plan": override.ref() if override else None,
            "exempt": person.exempt,
            **self._header(resolution),
            "limits": rows,
            "resolution": steps,
            "decided_by": resolution.decided_by if resolution.decided_by in (STEP_OVERRIDE, STEP_GROUPS, STEP_DEFAULT) else "none",
        }

    def _used_kinds(self, plans: Mapping[str, Plan]) -> List[str]:
        used = {limit.kind for plan in plans.values() for limit in plan.limits}
        return [kind.key for kind in self.registry.all() if kind.key in used]

    def users(self, q: Optional[str] = None, plan_id: Optional[str] = None, sort: Optional[str] = None,
              limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        settings = self.settings_store.read()
        plans = self.guard.plans_by_id()
        kinds = self._used_kinds(plans)
        subjects = self.plans.subjects()
        if q:
            needle = q.casefold()
            subjects = [s for s in subjects if needle in s.username.casefold() or needle in (s.email or "").casefold()]
        entries = []
        for person in self._people(subjects, plans, settings):
            primary = person.resolution.primary
            primary_id = primary.plan.id if primary else None
            if plan_id and (primary_id != plan_id if plan_id != "none" else primary_id is not None):
                continue
            rows = [self._row(person, self._effective(person, key), settings, admin_view=True) for key in kinds]
            rows = [row for row in rows if row is not None]
            percents = [row["percent"] for row in rows if row["percent"] is not None]
            entries.append({
                "user_id": person.subject.user_id,
                "username": person.subject.username,
                "email": person.subject.email,
                "account_type": person.subject.account_type,
                **self._header(person.resolution),
                "exempt": person.exempt,
                "max_percent": max(percents) if percents else None,
                "limits": rows,
            })
        entries.sort(key=self._sort_key(sort))
        total = len(entries)
        page = max(1, min(limit or 100, _MAX_PAGE))
        start = max(0, offset or 0)
        return {
            "users": entries[start:start + page],
            "kinds": [describe(self.registry.get(k), settings.day_timezone) for k in kinds],
            "total": total,
        }

    @staticmethod
    def _sort_key(sort: Optional[str]):
        if sort == "username":
            return lambda e: e["username"].casefold()
        if sort and sort != "most_used":
            def by_kind(entry):
                row = next((r for r in entry["limits"] if r["kind"] == sort), None)
                percent = row["percent"] if row and row["percent"] is not None else -1
                return (-percent, entry["username"].casefold())
            return by_kind
        return lambda e: (-(e["max_percent"] if e["max_percent"] is not None else -1), e["username"].casefold())
