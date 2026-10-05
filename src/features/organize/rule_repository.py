import json
from typing import Dict, Iterable, List, Optional, Sequence, Set

from src.features.organize.records import Controls, Rule
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

GLOBAL_SCOPE = "*"


def _db():
    from src.platform.database.database import db
    return db


class RuleCapReached(Exception):
    pass


class OrganizeRuleRepository:

    def create(self, rule: Rule, cap: int) -> Optional[Rule]:
        rule_id = generate_ulid()
        stamp = now_iso()
        with _db().get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_rules (id, user_id, name, subject, trigger, match_mode, conditions_json, "
                "actions_json, enabled, stop_after, position, created_at, updated_at) "
                "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "(SELECT COALESCE(MAX(position) + 1, 0) FROM organize_rules WHERE user_id = ? AND subject = ?), ?, ? "
                "WHERE (SELECT COUNT(*) FROM organize_rules WHERE user_id = ?) < ?",
                (
                    rule_id, rule.user_id, rule.name, rule.subject, rule.trigger, rule.match,
                    json.dumps(rule.conditions), json.dumps(rule.actions), int(rule.enabled),
                    int(rule.stop_after), rule.user_id, rule.subject, stamp, stamp, rule.user_id, cap,
                ),
            )
            inserted = cursor.rowcount > 0
        if not inserted:
            raise RuleCapReached()
        return self.get(rule.user_id, rule_id)

    def get(self, user_id: str, rule_id: str) -> Optional[Rule]:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT * FROM organize_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
            row = cursor.fetchone()
        return Rule.from_row(row) if row else None

    def list_for_user(self, user_id: str, subject: Optional[str] = None) -> List[Rule]:
        with _db().get_cursor() as cursor:
            if subject:
                cursor.execute(
                    "SELECT * FROM organize_rules WHERE user_id = ? AND subject = ? ORDER BY position, created_at, id",
                    (user_id, subject),
                )
            else:
                cursor.execute(
                    "SELECT * FROM organize_rules WHERE user_id = ? ORDER BY subject, position, created_at, id",
                    (user_id,),
                )
            rows = cursor.fetchall()
        return [Rule.from_row(row) for row in rows]

    def live_rules(self, user_id: str, subject: str) -> List[Rule]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM organize_rules WHERE user_id = ? AND subject = ? AND enabled = 1 "
                "ORDER BY position, created_at, id",
                (user_id, subject),
            )
            rows = cursor.fetchall()
        return [Rule.from_row(row) for row in rows]

    def users_with_live_rules(self, subject: str) -> List[str]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT user_id FROM organize_rules WHERE subject = ? AND enabled = 1 AND paused_reason IS NULL",
                (subject,),
            )
            return [row["user_id"] for row in cursor.fetchall()]

    def count_for_user(self, user_id: str) -> int:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM organize_rules WHERE user_id = ?", (user_id,))
            return cursor.fetchone()["n"]

    def update(self, rule: Rule, reset_handled: bool = False) -> Optional[Rule]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "UPDATE organize_rules SET name = ?, match_mode = ?, conditions_json = ?, actions_json = ?, "
                "enabled = ?, stop_after = ?, paused_reason = ?, paused_at = ?, updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (
                    rule.name, rule.match, json.dumps(rule.conditions), json.dumps(rule.actions),
                    int(rule.enabled), int(rule.stop_after), rule.paused_reason,
                    rule.paused_at.isoformat() if rule.paused_at else None, now_iso(), rule.id, rule.user_id,
                ),
            )
            updated = cursor.rowcount > 0
            if updated and reset_handled:
                cursor.execute(
                    "DELETE FROM organize_handled WHERE rule_id IN (SELECT id FROM organize_rules WHERE id = ? AND user_id = ?)",
                    (rule.id, rule.user_id),
                )
        return self.get(rule.user_id, rule.id) if updated else None

    def store_actions(self, user_id: str, rule_id: str, actions: List[Dict]) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "UPDATE organize_rules SET actions_json = ? WHERE id = ? AND user_id = ?",
                (json.dumps(actions), rule_id, user_id),
            )

    def pause(self, user_id: str, rule_id: str, reason: str) -> bool:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "UPDATE organize_rules SET paused_reason = ?, paused_at = ? "
                "WHERE id = ? AND user_id = ? AND paused_reason IS NULL",
                (reason, now_iso(), rule_id, user_id),
            )
            return cursor.rowcount > 0

    def touch_run(self, user_id: str, rule_id: str) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "UPDATE organize_rules SET last_run_at = ? WHERE id = ? AND user_id = ?",
                (now_iso(), rule_id, user_id),
            )

    def delete(self, user_id: str, rule_id: str) -> bool:
        with _db().get_cursor() as cursor:
            cursor.execute("DELETE FROM organize_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
            return cursor.rowcount > 0

    def reorder(self, user_id: str, subject: str, rule_ids: Sequence[str]) -> None:
        with _db().get_cursor() as cursor:
            for position, rule_id in enumerate(rule_ids):
                cursor.execute(
                    "UPDATE organize_rules SET position = ? WHERE id = ? AND user_id = ? AND subject = ?",
                    (position, rule_id, user_id, subject),
                )

    def handled_ids(self, rule_id: str, item_type: str, item_ids: Iterable[str]) -> Set[str]:
        ids = list(dict.fromkeys(item_ids))
        found: Set[str] = set()
        with _db().get_cursor() as cursor:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT item_id FROM organize_handled WHERE rule_id = ? AND item_type = ? AND item_id IN ({placeholders})",
                    (rule_id, item_type, *chunk),
                )
                found.update(row["item_id"] for row in cursor.fetchall())
        return found

    def mark_handled(self, rule_id: str, item_type: str, item_id: str) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO organize_handled (rule_id, item_type, item_id, handled_at) VALUES (?, ?, ?, ?)",
                (rule_id, item_type, item_id, now_iso()),
            )

    def controls(self, scope_key: str) -> Controls:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT * FROM organize_controls WHERE scope_key = ?", (scope_key,))
            row = cursor.fetchone()
        if not row:
            return Controls()
        return Controls(paused=bool(row["paused"]), rule_cap=row["rule_cap"], hourly_limit=row["hourly_limit"])

    def all_user_controls(self) -> Dict[str, Controls]:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT * FROM organize_controls WHERE scope_key != ?", (GLOBAL_SCOPE,))
            rows = cursor.fetchall()
        return {
            row["scope_key"]: Controls(paused=bool(row["paused"]), rule_cap=row["rule_cap"], hourly_limit=row["hourly_limit"])
            for row in rows
        }

    def save_controls(self, scope_key: str, controls: Controls) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_controls (scope_key, paused, rule_cap, hourly_limit, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(scope_key) DO UPDATE SET paused = excluded.paused, rule_cap = excluded.rule_cap, "
                "hourly_limit = excluded.hourly_limit, updated_at = excluded.updated_at",
                (scope_key, int(controls.paused), controls.rule_cap, controls.hourly_limit, now_iso()),
            )

    def rule_counts_by_user(self) -> Dict[str, tuple]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT user_id, COUNT(*) AS total, SUM(enabled) AS enabled, "
                "SUM(CASE WHEN paused_reason IS NOT NULL THEN 1 ELSE 0 END) AS paused, MAX(last_run_at) AS last_run_at "
                "FROM organize_rules GROUP BY user_id"
            )
            rows = cursor.fetchall()
        return {row["user_id"]: (row["total"], row["enabled"] or 0, row["paused"] or 0, row["last_run_at"]) for row in rows}

    def rules_targeting_collection(self, user_id: str, subject: str, collection_id: str) -> List[Rule]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT r.* FROM organize_rules r WHERE r.user_id = ? AND r.subject = ? AND EXISTS ("
                "SELECT 1 FROM json_each(r.actions_json) a "
                "WHERE json_extract(a.value, '$.action') = 'add_to_collection' "
                "AND json_extract(a.value, '$.config.collection_id') = ?) "
                "ORDER BY r.position, r.id",
                (user_id, subject, collection_id),
            )
            rows = cursor.fetchall()
        return [Rule.from_row(row) for row in rows]

    def usernames(self, user_ids: Iterable[str]) -> Dict[str, str]:
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with _db().get_cursor() as cursor:
            cursor.execute(f"SELECT id, username FROM users WHERE id IN ({placeholders})", ids)
            return {row["id"]: row["username"] for row in cursor.fetchall()}

    def user_exists(self, user_id: str) -> bool:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE id = ?", (user_id,))
            return cursor.fetchone() is not None

    def is_admin(self, user_id: str) -> bool:
        with _db().get_cursor() as cursor:
            cursor.execute("SELECT account_type FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
        return bool(row) and row["account_type"] == "ADMIN"
