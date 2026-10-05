import json
from datetime import timedelta
from typing import Any, Dict, List, Optional

from src.features.organize.records import Application, Run
from src.platform.database.rows import now_iso, now_utc
from src.platform.util.ids import generate_ulid

_RUN_SELECT = (
    "SELECT r.*, CASE WHEN EXISTS (SELECT 1 FROM organize_rules x WHERE x.id = r.rule_id) "
    "THEN 0 ELSE 1 END AS rule_deleted FROM organize_runs r"
)


def _db():
    from src.platform.database.database import db
    return db


class OrganizeRunRepository:

    def create_run(self, rule_id: str, rule_name: str, user_id: str, subject: str, kind: str,
                   status: str = "running", matched: int = 0) -> Run:
        run_id = generate_ulid()
        with _db().get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_runs (id, rule_id, rule_name, user_id, subject, kind, status, matched, applied, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (run_id, rule_id, rule_name, user_id, subject, kind, status, matched, now_iso()),
            )
        return self.get_run(user_id, run_id)

    def open_live_run(self, rule_id: str, rule_name: str, user_id: str, subject: str) -> Run:
        since = (now_utc() - timedelta(hours=1)).isoformat()
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT id FROM organize_runs WHERE rule_id = ? AND user_id = ? AND kind = 'live' "
                "AND undone_at IS NULL AND status = 'completed' AND started_at >= ? ORDER BY started_at DESC, id DESC LIMIT 1",
                (rule_id, user_id, since),
            )
            row = cursor.fetchone()
        if row:
            return self.get_run(user_id, row["id"])
        return self.create_run(rule_id, rule_name, user_id, subject, "live", status="completed")

    def get_run(self, user_id: str, run_id: str) -> Optional[Run]:
        with _db().get_cursor() as cursor:
            cursor.execute(f"{_RUN_SELECT} WHERE r.id = ? AND r.user_id = ?", (run_id, user_id))
            row = cursor.fetchone()
        return Run.from_row(row) if row else None

    def bump_run(self, user_id: str, run_id: str, matched: int, applied: int) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "UPDATE organize_runs SET matched = matched + ?, applied = applied + ?, finished_at = ? "
                "WHERE id = ? AND user_id = ?",
                (matched, applied, now_iso(), run_id, user_id),
            )

    def finish_run(self, user_id: str, run_id: str, status: str, matched: Optional[int] = None) -> None:
        with _db().get_cursor() as cursor:
            if matched is None:
                cursor.execute(
                    "UPDATE organize_runs SET status = ?, finished_at = ? WHERE id = ? AND user_id = ?",
                    (status, now_iso(), run_id, user_id),
                )
            else:
                cursor.execute(
                    "UPDATE organize_runs SET status = ?, matched = ?, finished_at = ? WHERE id = ? AND user_id = ?",
                    (status, matched, now_iso(), run_id, user_id),
                )

    def record(self, run: Run, item_type: str, item_id: str, action_kind: str, target_type: str,
               target_id: str, target_name: str, change: Dict[str, Any]) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_applications (id, run_id, rule_id, user_id, item_type, item_id, action_kind, "
                "target_type, target_id, target_name, change_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    generate_ulid(), run.id, run.rule_id, run.user_id, item_type, item_id, action_kind,
                    target_type, target_id, target_name, json.dumps(change), now_iso(),
                ),
            )

    def live_items_last_hour(self, user_id: str, rule_id: str) -> int:
        since = (now_utc() - timedelta(hours=1)).isoformat()
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(DISTINCT a.item_type || ':' || a.item_id) AS n FROM organize_applications a "
                "JOIN organize_runs r ON r.id = a.run_id "
                "WHERE a.rule_id = ? AND a.user_id = ? AND r.kind = 'live' AND a.created_at >= ? AND a.undone_at IS NULL",
                (rule_id, user_id, since),
            )
            return cursor.fetchone()["n"]

    def filed_counts(self, user_id: str, rule_ids: List[str]) -> Dict[str, int]:
        if not rule_ids:
            return {}
        placeholders = ",".join("?" * len(rule_ids))
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"SELECT rule_id, COUNT(DISTINCT item_type || ':' || item_id) AS n FROM organize_applications "
                f"WHERE user_id = ? AND undone_at IS NULL AND rule_id IN ({placeholders}) GROUP BY rule_id",
                (user_id, *rule_ids),
            )
            return {row["rule_id"]: row["n"] for row in cursor.fetchall()}

    def list_runs(self, user_id: str, subject: Optional[str], rule_id: Optional[str],
                  before: Optional[str], limit: int) -> List[Run]:
        clauses = ["r.user_id = ?"]
        params: List[Any] = [user_id]
        if subject:
            clauses.append("r.subject = ?")
            params.append(subject)
        if rule_id:
            clauses.append("r.rule_id = ?")
            params.append(rule_id)
        if before:
            clauses.append("r.id < ?")
            params.append(before)
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"{_RUN_SELECT} WHERE {' AND '.join(clauses)} AND (r.matched > 0 OR r.kind = 'backfill') "
                f"ORDER BY r.id DESC LIMIT ?",
                (*params, limit),
            )
            rows = cursor.fetchall()
        return [Run.from_row(row) for row in rows]

    def run_change_summary(self, user_id: str, run_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        if not run_ids:
            return {}
        placeholders = ",".join("?" * len(run_ids))
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"SELECT run_id, action_kind, target_type, target_id, MAX(target_name) AS target_name, COUNT(*) AS total, "
                f"SUM(CASE WHEN undone_at IS NULL THEN 0 ELSE 1 END) AS undone, "
                f"MAX(CASE WHEN json_extract(change_json, '$.created') THEN 1 ELSE 0 END) AS created "
                f"FROM organize_applications WHERE user_id = ? AND run_id IN ({placeholders}) "
                f"GROUP BY run_id, action_kind, target_type, target_id",
                (user_id, *run_ids),
            )
            rows = cursor.fetchall()
        summary: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            summary.setdefault(row["run_id"], []).append(dict(row))
        return summary

    def run_undone_items(self, user_id: str, run_ids: List[str]) -> Dict[str, int]:
        if not run_ids:
            return {}
        placeholders = ",".join("?" * len(run_ids))
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"SELECT run_id, COUNT(DISTINCT item_type || ':' || item_id) AS n FROM organize_applications "
                f"WHERE user_id = ? AND run_id IN ({placeholders}) AND undone_at IS NOT NULL GROUP BY run_id",
                (user_id, *run_ids),
            )
            return {row["run_id"]: row["n"] for row in cursor.fetchall()}

    def pending_applications(self, user_id: str, run_id: str) -> List[Application]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM organize_applications WHERE user_id = ? AND run_id = ? AND undone_at IS NULL ORDER BY id",
                (user_id, run_id),
            )
            rows = cursor.fetchall()
        return [Application.from_row(row) for row in rows]

    def mark_undone(self, user_id: str, application_ids: List[str]) -> None:
        stamp = now_iso()
        with _db().get_cursor() as cursor:
            for start in range(0, len(application_ids), 500):
                chunk = application_ids[start:start + 500]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"UPDATE organize_applications SET undone_at = ? WHERE user_id = ? AND id IN ({placeholders})",
                    (stamp, user_id, *chunk),
                )

    def mark_run_undone(self, user_id: str, run_id: str) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "UPDATE organize_runs SET status = 'undone', undone_at = ? WHERE id = ? AND user_id = ?",
                (now_iso(), run_id, user_id),
            )

    def provenance(self, user_id: str, item_type: str, item_id: str) -> List[Dict[str, Any]]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT a.rule_id, a.run_id, a.action_kind, a.target_type, a.target_id, a.target_name, a.created_at, "
                "COALESCE(x.name, r.rule_name) AS rule_name, CASE WHEN x.id IS NULL THEN 1 ELSE 0 END AS rule_deleted "
                "FROM organize_applications a JOIN organize_runs r ON r.id = a.run_id "
                "LEFT JOIN organize_rules x ON x.id = a.rule_id "
                "WHERE a.user_id = ? AND a.item_type = ? AND a.item_id = ? AND a.undone_at IS NULL "
                "ORDER BY a.created_at DESC, a.id DESC",
                (user_id, item_type, item_id),
            )
            return [dict(row) for row in cursor.fetchall()]

    def filed_totals_by_user(self) -> Dict[str, Dict[str, int]]:
        since = (now_utc() - timedelta(hours=24)).isoformat()
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT user_id, COUNT(DISTINCT item_type || ':' || item_id) AS total, "
                "COUNT(DISTINCT CASE WHEN created_at >= ? THEN item_type || ':' || item_id END) AS day "
                "FROM organize_applications WHERE undone_at IS NULL GROUP BY user_id",
                (since,),
            )
            return {row["user_id"]: {"total": row["total"], "day": row["day"]} for row in cursor.fetchall()}

    def prune(self, days: int = 90) -> int:
        cutoff = (now_utc() - timedelta(days=days)).isoformat()
        with _db().get_cursor() as cursor:
            cursor.execute("DELETE FROM organize_runs WHERE started_at < ?", (cutoff,))
            return cursor.rowcount
