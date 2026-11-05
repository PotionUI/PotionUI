from typing import Any, Dict, Optional

from src.features.organize import errors
from src.features.organize.manager import DEFAULT_HOURLY_LIMIT, OrganizeManager
from src.features.organize.records import Controls
from src.features.organize.rule_repository import GLOBAL_SCOPE, INSTANCE_SCOPE
from src.platform.database.rows import dt_iso


class OrganizeAdmin:

    def __init__(self, organize: OrganizeManager):
        self.o = organize

    def overview(self) -> Dict[str, Any]:
        controls = self.o.global_controls()
        counts = self.o.c.rules.rule_counts_by_user()
        user_controls = self.o.c.rules.all_user_controls()
        filed = self.o.c.runs.filed_totals_by_user()
        user_ids = list(dict.fromkeys(list(counts) + list(user_controls)))
        usernames = self.o.c.rules.usernames(user_ids)
        default_cap = self.o.default_cap()
        users = []
        for user_id in user_ids:
            if user_id not in usernames:
                continue
            users.append(self._row(user_id, usernames[user_id], counts.get(user_id), user_controls.get(user_id),
                               filed.get(user_id), default_cap))
        totals = {
            "users_with_rules": sum(1 for row in users if row["rules"]),
            "rules": sum(row["rules"] for row in users),
            "enabled_rules": sum(row["enabled_rules"] for row in users),
            "paused_rules": sum(row["paused_rules"] for row in users),
            "items_filed_24h": sum(row["items_filed_24h"] for row in users),
            "items_filed_total": sum(row["items_filed_total"] for row in users),
            "running_jobs": self.o.jobs.running_count(),
        }
        return {
            "paused_all": controls.paused,
            "default_rule_cap": default_cap,
            "hourly_limit": controls.hourly_limit or DEFAULT_HOURLY_LIMIT,
            "totals": totals,
            "users": users,
            "instance": self.instance_row(),
        }

    def instance_row(self) -> Dict[str, Any]:
        total, enabled, paused, last_run_at = self.o.c.rules.instance_counts()
        controls = self.o.c.rules.controls(INSTANCE_SCOPE)
        return {
            "rules": total,
            "enabled_rules": enabled,
            "paused_rules": paused,
            "paused": controls.paused,
            "rule_cap": controls.rule_cap,
            "effective_rule_cap": self.o.effective_cap(None),
            "last_run_at": dt_iso(last_run_at),
            "running_jobs": sum(1 for job in self.o.jobs.for_user(None, True)),
        }

    def instance_controls(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        controls = self.o.c.rules.controls(INSTANCE_SCOPE)
        if payload.get("paused") is not None:
            controls.paused = bool(payload["paused"])
        if "rule_cap" in payload:
            controls.rule_cap = None if payload["rule_cap"] is None else int(payload["rule_cap"])
        self.o.c.rules.save_controls(INSTANCE_SCOPE, controls)
        if controls.paused:
            self.o.jobs.cancel_for_user(None)
        return self.instance_row()

    def _row(self, user_id: str, username: str, counts: Optional[tuple], controls: Optional[Controls],
                   filed: Optional[Dict[str, int]], default_cap: int) -> Dict[str, Any]:
        total, enabled, paused, last_run_at = counts or (0, 0, 0, None)
        controls = controls or Controls()
        return {
            "user_id": user_id,
            "username": username,
            "rules": total,
            "enabled_rules": enabled,
            "paused_rules": paused,
            "items_filed_24h": (filed or {}).get("day", 0),
            "items_filed_total": (filed or {}).get("total", 0),
            "paused": controls.paused,
            "rule_cap": controls.rule_cap,
            "effective_rule_cap": default_cap if controls.rule_cap is None else controls.rule_cap,
            "last_run_at": dt_iso(last_run_at),
        }

    def controls(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        controls = self.o.global_controls()
        if payload.get("paused_all") is not None:
            controls.paused = bool(payload["paused_all"])
        if payload.get("default_rule_cap") is not None:
            controls.rule_cap = int(payload["default_rule_cap"])
        if payload.get("hourly_limit") is not None:
            controls.hourly_limit = int(payload["hourly_limit"])
        self.o.c.rules.save_controls(GLOBAL_SCOPE, controls)
        if controls.paused:
            self.o.jobs.cancel_all()
        return self.overview()

    def user_controls(self, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if user_id == GLOBAL_SCOPE or self.o.c.users.get_by_id(user_id) is None:
            raise errors.OrganizeError("user_not_found", "User not found", 404)
        controls = self.o.c.rules.controls(user_id)
        if payload.get("paused") is not None:
            controls.paused = bool(payload["paused"])
        if "rule_cap" in payload:
            controls.rule_cap = None if payload["rule_cap"] is None else int(payload["rule_cap"])
        self.o.c.rules.save_controls(user_id, controls)
        if controls.paused:
            self.o.jobs.cancel_for_user(user_id)
        return next((u for u in self.overview()["users"] if u["user_id"] == user_id), None)
