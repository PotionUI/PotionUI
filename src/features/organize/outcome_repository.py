from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.features.organize.records import Outcome
from src.platform.database.rows import dt_iso, now_iso, now_utc


def _db():
    from src.platform.database.database import db
    return db


class OrganizeOutcomeRepository:

    def get(self, rule_id: str, item_type: str, item_id: str) -> Optional[Outcome]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM organize_outcomes WHERE rule_id = ? AND item_type = ? AND item_id = ?",
                (rule_id, item_type, item_id),
            )
            row = cursor.fetchone()
        return Outcome.from_row(row) if row else None

    def upsert(self, outcome: Outcome) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO organize_outcomes (rule_id, item_type, item_id, run_id, outcome, code, detail, attempts, "
                "next_try_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(rule_id, item_type, item_id) DO UPDATE SET run_id = excluded.run_id, "
                "outcome = excluded.outcome, code = excluded.code, detail = excluded.detail, "
                "attempts = excluded.attempts, next_try_at = excluded.next_try_at, updated_at = excluded.updated_at",
                (
                    outcome.rule_id, outcome.item_type, outcome.item_id, outcome.run_id, outcome.outcome,
                    outcome.code, outcome.detail, outcome.attempts, dt_iso(outcome.next_try_at), now_iso(),
                ),
            )

    def clear(self, rule_id: str, item_type: str, item_id: str) -> None:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM organize_outcomes WHERE rule_id = ? AND item_type = ? AND item_id = ?",
                (rule_id, item_type, item_id),
            )

    def counts_by_rule(self, rule_ids: Sequence[str]) -> Dict[str, Dict[str, int]]:
        if not rule_ids:
            return {}
        placeholders = ",".join("?" * len(rule_ids))
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"SELECT rule_id, outcome, COUNT(*) AS n FROM organize_outcomes WHERE rule_id IN ({placeholders}) "
                f"GROUP BY rule_id, outcome",
                list(rule_ids),
            )
            rows = cursor.fetchall()
        counts: Dict[str, Dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["rule_id"], {})[row["outcome"]] = row["n"]
        return counts

    def counts_by_run(self, run_ids: Sequence[str]) -> Dict[str, Dict[str, int]]:
        if not run_ids:
            return {}
        placeholders = ",".join("?" * len(run_ids))
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"SELECT run_id, outcome, COUNT(*) AS n FROM organize_outcomes WHERE run_id IN ({placeholders}) "
                f"GROUP BY run_id, outcome",
                list(run_ids),
            )
            rows = cursor.fetchall()
        counts: Dict[str, Dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["run_id"], {})[row["outcome"]] = row["n"]
        return counts

    def reasons_by_run(self, run_ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        if not run_ids:
            return {}
        placeholders = ",".join("?" * len(run_ids))
        with _db().get_cursor() as cursor:
            cursor.execute(
                f"SELECT run_id, outcome, code, MAX(detail) AS detail, COUNT(*) AS n FROM organize_outcomes "
                f"WHERE run_id IN ({placeholders}) GROUP BY run_id, outcome, code ORDER BY n DESC, code",
                list(run_ids),
            )
            rows = cursor.fetchall()
        reasons: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            reasons.setdefault(row["run_id"], []).append(
                {"outcome": row["outcome"], "code": row["code"], "detail": row["detail"], "count": row["n"]}
            )
        return reasons

    def run_items(self, user_id: Optional[str], run_id: str, status: Optional[str], limit: int,
                  offset: int) -> Dict[str, Any]:
        inner = (
            "SELECT a.item_type AS item_type, a.item_id AS item_id, 'updated' AS status, MIN(a.action_kind) AS code, "
            "MAX(a.target_name) AS detail, 0 AS attempts, NULL AS next_try_at, MIN(a.created_at) AS at "
            "FROM organize_applications a WHERE a.run_id = ? AND a.user_id IS ? GROUP BY a.item_type, a.item_id "
            "UNION ALL "
            "SELECT o.item_type, o.item_id, o.outcome, o.code, o.detail, o.attempts, o.next_try_at, o.updated_at "
            "FROM organize_outcomes o WHERE o.run_id = ?"
        )
        where = "WHERE status = ?" if status else ""
        params: List[Any] = [run_id, user_id, run_id]
        if status:
            params.append(status)
        with _db().get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS n FROM ({inner}) {where}", params)
            total = cursor.fetchone()["n"]
            cursor.execute(
                f"SELECT * FROM ({inner}) {where} ORDER BY status, at, item_id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )
            rows = [dict(row) for row in cursor.fetchall()]
        return {"total": total, "rows": rows}

    def due(self, now: datetime, limit: int) -> List[Outcome]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM organize_outcomes WHERE outcome = 'waiting' AND "
                "((next_try_at IS NOT NULL AND datetime(next_try_at) <= datetime(?)) OR "
                "(next_try_at IS NULL AND code = 'unverified' AND item_type = 'model' AND EXISTS ("
                "SELECT 1 FROM models m WHERE m.id = organize_outcomes.item_id AND m.sha256 IS NOT NULL AND m.sha256 != ''))) "
                "ORDER BY rule_id, updated_at LIMIT ?",
                (dt_iso(now), limit),
            )
            rows = cursor.fetchall()
        return [Outcome.from_row(row) for row in rows]

    def waiting_for_run(self, run_id: str) -> List[Outcome]:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM organize_outcomes WHERE run_id = ? AND outcome = 'waiting' ORDER BY updated_at, item_id",
                (run_id,),
            )
            rows = cursor.fetchall()
        return [Outcome.from_row(row) for row in rows]

    def waiting_for_items(self, item_type: str, item_ids: Iterable[str]) -> List[Outcome]:
        ids = list(dict.fromkeys(item_ids))
        found: List[Outcome] = []
        with _db().get_cursor() as cursor:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"SELECT * FROM organize_outcomes WHERE outcome = 'waiting' AND item_type = ? "
                    f"AND item_id IN ({placeholders})",
                    (item_type, *chunk),
                )
                found.extend(Outcome.from_row(row) for row in cursor.fetchall())
        return found

    def release_run(self, run_id: str) -> int:
        with _db().get_cursor() as cursor:
            cursor.execute(
                "UPDATE organize_outcomes SET next_try_at = ? WHERE run_id = ? AND outcome = 'waiting'",
                (dt_iso(now_utc() - timedelta(seconds=1)), run_id),
            )
            return cursor.rowcount

    def purge_items(self, item_type: str, item_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(item_ids))
        with _db().get_cursor() as cursor:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                placeholders = ",".join("?" * len(chunk))
                cursor.execute(
                    f"DELETE FROM organize_outcomes WHERE item_type = ? AND item_id IN ({placeholders})",
                    (item_type, *chunk),
                )
