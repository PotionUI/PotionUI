import json
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from src.features.elixirs.follow import STALE_SESSION, STALE_UPSTREAM, is_following
from src.features.elixirs.records import (
    ACTIVE_INVOCATION_STATUSES,
    ACTIVE_RUN_STATUSES,
    ELIXIRS_TABLE,
    INVOCATIONS_TABLE,
    RUNS_TABLE,
    utc_now,
)
from src.features.elixirs.vocabulary import UNTITLED, node_label
from src.platform.util.ids import generate_ulid

RUN_JSON_FIELDS = ("graph", "templates", "capability_digests", "pinned_inputs", "node_states", "checkpoints")
INVOCATION_JSON_FIELDS = ("input_ref", "request", "outputs")


STATUS_GROUPS = {
    "active": ACTIVE_RUN_STATUSES,
    "finished": ("completed", "partial"),
    "failed": ("failed", "interrupted"),
    "cancelled": ("cancelled",),
}
CANCELLABLE_RUN_STATUSES = ("pending", "running", "awaiting_selection", "needs_attention")
DAY_START = " 00:00:00"
DAY_END = " 23:59:59"

def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def _step_title_sql() -> str:
    kinds = " ".join(f"WHEN '{kind}' THEN '{label}'" for kind, label in UNTITLED.items())
    return (
        "COALESCE(NULLIF(TRIM(json_extract(n.value, '$.title')), ''), "
        f"CASE json_extract(n.value, '$.kind') {kinds} ELSE 'Untitled preset node' END)"
    )

def _placeholders(values) -> str:
    return ", ".join("?" for _ in values)


def _run_from_row(row) -> Dict[str, Any]:
    run = dict(row)
    for field in RUN_JSON_FIELDS:
        run[field] = json.loads(run[field]) if run.get(field) else {}
    return run


def _decode(value) -> Dict[str, Any]:
    try:
        decoded = json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}

def _invocation_from_row(row) -> Dict[str, Any]:
    invocation = dict(row)
    for field in INVOCATION_JSON_FIELDS:
        value = invocation.get(field)
        invocation[field] = json.loads(value) if value else ([] if field == "outputs" else None)
    return invocation


def _encode(fields: Dict[str, Any], json_fields) -> Dict[str, Any]:
    return {key: (json.dumps(value) if key in json_fields and value is not None else value) for key, value in fields.items()}


class RunStore:
    @contextmanager
    def transaction(self) -> Iterator[Any]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute("BEGIN IMMEDIATE")
            yield cursor

    def insert_run(self, cursor, fields: Dict[str, Any]) -> str:
        run_id = fields.get("id") or generate_ulid()
        now = utc_now()
        row = _encode({**fields, "id": run_id, "created_at": now, "updated_at": now}, RUN_JSON_FIELDS)
        if row.get("elixir_id") and "number" not in row:
            cursor.execute(f"SELECT COALESCE(MAX(number), 0) + 1 FROM {RUNS_TABLE} WHERE elixir_id = ?", (row["elixir_id"],))
            row["number"] = cursor.fetchone()[0]
        cursor.execute(
            f"INSERT INTO {RUNS_TABLE} ({', '.join(row)}) VALUES ({_placeholders(row)})",
            tuple(row.values()),
        )
        return run_id

    def find_run_by_key(self, cursor, user_id: str, idempotency_key: str) -> Optional[Dict[str, Any]]:
        cursor.execute(
            f"SELECT * FROM {RUNS_TABLE} WHERE user_id = ? AND idempotency_key = ?",
            (user_id, idempotency_key),
        )
        row = cursor.fetchone()
        return _run_from_row(row) if row else None

    def load_run(self, cursor, run_id: str) -> Optional[Dict[str, Any]]:
        cursor.execute(f"SELECT * FROM {RUNS_TABLE} WHERE id = ?", (run_id,))
        row = cursor.fetchone()
        return _run_from_row(row) if row else None

    def save_run(self, cursor, run_id: str, fields: Dict[str, Any]) -> None:
        row = _encode({**fields, "updated_at": utc_now()}, RUN_JSON_FIELDS)
        assignments = ", ".join(f"{key} = ?" for key in row)
        cursor.execute(f"UPDATE {RUNS_TABLE} SET {assignments} WHERE id = ?", (*row.values(), run_id))

    def get_run(self, run_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            run = self.load_run(cursor, run_id)
        if run is None or (user_id is not None and run["user_id"] != user_id):
            return None
        return run

    def list_runs(self, elixir_id: str, user_id: str) -> List[Dict[str, Any]]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, elixir_id, elixir_revision, scope, scope_node_id, scope_edge_id, base_run_id,
                       run_mode, status, error, created_at, updated_at, finished_at
                FROM {RUNS_TABLE} WHERE elixir_id = ? AND user_id = ? ORDER BY created_at DESC, id DESC
                """,
                (elixir_id, user_id),
            )
            return [dict(row) for row in cursor.fetchall()]

    def page_runs(
        self,
        elixir_id: str,
        user_id: str,
        *,
        status: str = "all",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        run_mode: Optional[str] = None,
        scope: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        from src.platform.database.database import db

        conditions = ["r.elixir_id = ?", "r.user_id = ?"]
        params: List[Any] = [elixir_id, user_id]
        if status in STATUS_GROUPS:
            conditions.append(f"r.status IN ({_placeholders(STATUS_GROUPS[status])})")
            params.extend(STATUS_GROUPS[status])
        if date_from:
            conditions.append("datetime(r.created_at) >= datetime(?)")
            params.append(date_from + DAY_START)
        if date_to:
            conditions.append("datetime(r.created_at) <= datetime(?)")
            params.append(date_to + DAY_END)
        if run_mode:
            conditions.append("r.run_mode = ?")
            params.append(run_mode)
        if scope == "node":
            conditions.append("r.scope = 'node'")
        elif scope == "elixir":
            conditions.append("r.scope <> 'node'")
        text = (query or "").strip().lstrip("#").strip()
        if text:
            title = (
                "r.scope = 'node' AND EXISTS (SELECT 1 FROM json_each(r.graph, '$.nodes') n "
                f"WHERE json_extract(n.value, '$.id') = r.scope_node_id AND LOWER({_step_title_sql()}) LIKE ? ESCAPE '\\')"
            )
            like = f"%{_escape_like(text.lower())}%"
            if text.isdigit():
                conditions.append(f"(r.number = ? OR ({title}))")
                params.extend([int(text), like])
            else:
                conditions.append(f"({title})")
                params.append(like)
        where = " AND ".join(conditions)
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {RUNS_TABLE} r WHERE {where}", params)
            total = cursor.fetchone()[0]
            cursor.execute(
                f"SELECT COUNT(*) FROM {RUNS_TABLE} WHERE elixir_id = ? AND user_id = ? AND status IN ({_placeholders(ACTIVE_RUN_STATUSES)})",
                (elixir_id, user_id, *ACTIVE_RUN_STATUSES),
            )
            active = cursor.fetchone()[0]
            cursor.execute(
                f"""
                SELECT r.id, r.elixir_id, r.number, r.elixir_revision, r.scope, r.scope_node_id, r.scope_edge_id, r.base_run_id,
                       r.run_mode, r.status, r.error, r.created_at, r.updated_at, r.finished_at, r.graph, r.node_states
                FROM {RUNS_TABLE} r WHERE {where} ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            totals: Dict[str, int] = {}
            if rows:
                ids = [row["id"] for row in rows]
                cursor.execute(
                    f"SELECT run_id, SUM(json_array_length(outputs)) AS total FROM {INVOCATIONS_TABLE} "
                    f"WHERE run_id IN ({_placeholders(ids)}) AND status = 'completed' GROUP BY run_id",
                    ids,
                )
                totals = {row["run_id"]: int(row["total"] or 0) for row in cursor.fetchall()}
        runs = []
        for row in rows:
            graph = _decode(row.pop("graph"))
            states = _decode(row.pop("node_states"))
            nodes = [node for node in graph.get("nodes") or [] if node.get("kind") != "media"]
            tracked = [node for node in nodes if node.get("id") in states]
            scoped = next((node for node in graph.get("nodes") or [] if node.get("id") == row["scope_node_id"]), None)
            runs.append({
                **row,
                "scope_label": node_label(scoped) if scoped is not None and row["scope"] == "node" else None,
                "steps_total": len(tracked),
                "steps_done": sum(1 for node in tracked if states[node["id"]].get("status") == "done"),
                "outputs": totals.get(row["id"], 0),
            })
        return {"runs": runs, "total": total, "active": active}

    def cancellable_run_ids(self, elixir_id: str, user_id: str, run_ids: Optional[List[str]] = None) -> List[str]:
        from src.platform.database.database import db

        conditions = ["elixir_id = ?", "user_id = ?", f"status IN ({_placeholders(CANCELLABLE_RUN_STATUSES)})"]
        params: List[Any] = [elixir_id, user_id, *CANCELLABLE_RUN_STATUSES]
        if run_ids is not None:
            if not run_ids:
                return []
            conditions.append(f"id IN ({_placeholders(run_ids)})")
            params.extend(run_ids)
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT id FROM {RUNS_TABLE} WHERE {' AND '.join(conditions)} ORDER BY created_at, id", params)
            return [row["id"] for row in cursor.fetchall()]

    def active_run_ids(self) -> List[str]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM {RUNS_TABLE} WHERE status IN ({_placeholders(ACTIVE_RUN_STATUSES)}) ORDER BY created_at",
                ACTIVE_RUN_STATUSES,
            )
            return [row["id"] for row in cursor.fetchall()]

    def cancelling_run_ids(self) -> List[str]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM {RUNS_TABLE} WHERE cancel_requested_at IS NOT NULL "
                f"AND status IN ({_placeholders(ACTIVE_RUN_STATUSES)}) ORDER BY created_at",
                ACTIVE_RUN_STATUSES,
            )
            return [row["id"] for row in cursor.fetchall()]

    def runs_with_active_invocations(self) -> set:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT DISTINCT run_id FROM {INVOCATIONS_TABLE} WHERE status IN ({_placeholders(ACTIVE_INVOCATION_STATUSES)})",
                ACTIVE_INVOCATION_STATUSES,
            )
            return {row["run_id"] for row in cursor.fetchall()}

    def release_claims(self) -> None:
        with self.transaction() as cursor:
            cursor.execute(
                f"SELECT id FROM {RUNS_TABLE} WHERE cancel_requested_at IS NOT NULL "
                f"AND status IN ({_placeholders(ACTIVE_RUN_STATUSES)})",
                ACTIVE_RUN_STATUSES,
            )
            now = utc_now()
            for row in cursor.fetchall():
                self.update_invocations_in(cursor, row["id"], ("pending", "claimed"), {"status": "cancelled", "finished_at": now})
            cursor.execute(
                f"UPDATE {INVOCATIONS_TABLE} SET status = 'pending', updated_at = ? WHERE status = 'claimed'",
                (now,),
            )

    def insert_invocation(self, cursor, fields: Dict[str, Any]) -> bool:
        now = utc_now()
        row = _encode({"id": generate_ulid(), "created_at": now, "updated_at": now, **fields}, INVOCATION_JSON_FIELDS)
        cursor.execute(
            f"INSERT OR IGNORE INTO {INVOCATIONS_TABLE} ({', '.join(row)}) VALUES ({_placeholders(row)})",
            tuple(row.values()),
        )
        return cursor.rowcount == 1

    def run_invocations(self, cursor, run_id: str) -> List[Dict[str, Any]]:
        cursor.execute(
            f"SELECT * FROM {INVOCATIONS_TABLE} WHERE run_id = ? ORDER BY node_id, ordinal, attempt",
            (run_id,),
        )
        return [_invocation_from_row(row) for row in cursor.fetchall()]

    def invocations(self, run_id: str) -> List[Dict[str, Any]]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            return self.run_invocations(cursor, run_id)

    def inherited_from_results(self, run: Dict[str, Any]) -> Dict[str, Any]:
        from src.platform.database.database import db

        inherited: Dict[str, Any] = {"node_states": {}, "checkpoints": {}, "edges": [], "invocations": []}
        if run["scope"] != "node":
            return inherited
        with db.get_cursor() as cursor:
            results = self.results_for(cursor, run["elixir_id"])
            sources: Dict[str, Dict[str, Any]] = {}
            for node_id, result in results.items():
                source_id = (result or {}).get("run_id")
                if node_id in run["node_states"] or not source_id or source_id == run["id"]:
                    continue
                if source_id not in sources:
                    sources[source_id] = self.load_run(cursor, source_id)
                source = sources[source_id]
                state = ((source or {}).get("node_states") or {}).get(node_id)
                if state is None:
                    continue
                inherited["node_states"][node_id] = state
                for edge in source["graph"].get("edges", []):
                    checkpoint = source["checkpoints"].get(edge["id"])
                    if edge["source"] == node_id and checkpoint and checkpoint.get("confirmed") is not None and edge["id"] not in inherited["checkpoints"]:
                        inherited["checkpoints"][edge["id"]] = checkpoint
                        inherited["edges"].append(edge)
                inherited["invocations"].extend(item for item in self.run_invocations(cursor, source_id) if item["node_id"] == node_id)
            drawn_in = (run.get("pinned_inputs") or {}).get("source_run_id")
            if drawn_in:
                if drawn_in not in sources:
                    sources[drawn_in] = self.load_run(cursor, drawn_in)
                source = sources[drawn_in] or {}
                for node in (source.get("graph") or {}).get("nodes", []):
                    state = (source.get("node_states") or {}).get(node["id"])
                    if node.get("kind") == "media" and (state or {}).get("draw") and node["id"] not in run["node_states"]:
                        inherited["node_states"].setdefault(node["id"], state)
        return inherited

    def invocations_with_status(self, statuses) -> List[Dict[str, Any]]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {INVOCATIONS_TABLE} WHERE status IN ({_placeholders(statuses)}) ORDER BY created_at, id",
                tuple(statuses),
            )
            return [_invocation_from_row(row) for row in cursor.fetchall()]

    def run_for_generation(self, generation_id: str) -> Optional[str]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT run_id FROM {INVOCATIONS_TABLE} WHERE generation_id = ?", (generation_id,))
            row = cursor.fetchone()
        return row["run_id"] if row else None

    def update_invocation(self, invocation_id: str, fields: Dict[str, Any], expected_status=None) -> bool:
        from src.platform.database.database import db

        row = _encode({**fields, "updated_at": utc_now()}, INVOCATION_JSON_FIELDS)
        assignments = ", ".join(f"{key} = ?" for key in row)
        params = [*row.values(), invocation_id]
        where = "id = ?"
        if expected_status is not None:
            statuses = (expected_status,) if isinstance(expected_status, str) else tuple(expected_status)
            where += f" AND status IN ({_placeholders(statuses)})"
            params.extend(statuses)
        with db.get_cursor() as cursor:
            cursor.execute(f"UPDATE {INVOCATIONS_TABLE} SET {assignments} WHERE {where}", params)
            return cursor.rowcount == 1

    def update_invocations_in(self, cursor, run_id: str, statuses, fields: Dict[str, Any]) -> int:
        row = _encode({**fields, "updated_at": utc_now()}, INVOCATION_JSON_FIELDS)
        assignments = ", ".join(f"{key} = ?" for key in row)
        statuses = tuple(statuses)
        cursor.execute(
            f"UPDATE {INVOCATIONS_TABLE} SET {assignments} WHERE run_id = ? AND status IN ({_placeholders(statuses)})",
            (*row.values(), run_id, *statuses),
        )
        return cursor.rowcount

    def generation_holders(self, generation_ids) -> Dict[str, List[Dict[str, Any]]]:
        wanted = {generation_id for generation_id in generation_ids if generation_id}
        if not wanted:
            return {}
        holders: Dict[str, List[str]] = {}
        for run_id in self.active_run_ids():
            run = self.get_run(run_id)
            if run is None:
                continue
            held: set = set()
            for checkpoint in run["checkpoints"].values():
                if checkpoint.get("confirmed") is None:
                    held.update(entry.get("generation_id") for entry in checkpoint.get("eligible") or [])
            for item in self.invocations(run_id):
                if item["status"] not in ACTIVE_INVOCATION_STATUSES:
                    continue
                held.add(item.get("generation_id"))
                held.add((item.get("input_ref") or {}).get("generation_id"))
            for generation_id in wanted & held:
                holders.setdefault(generation_id, []).append(run_id)
        rows = {row["run_id"]: row for row in self._holder_rows(sorted({run_id for ids in holders.values() for run_id in ids}))}
        return {generation_id: [rows[run_id] for run_id in ids if run_id in rows] for generation_id, ids in holders.items()}

    def _holder_rows(self, run_ids: List[str]) -> List[Dict[str, Any]]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT r.id AS run_id, r.elixir_id AS elixir_id, r.number AS number, e.name AS elixir_name
                FROM {RUNS_TABLE} r LEFT JOIN {ELIXIRS_TABLE} e ON e.id = r.elixir_id
                WHERE r.id IN ({_placeholders(run_ids)}) ORDER BY r.created_at, r.id
                """,
                run_ids,
            )
            return [dict(row) for row in cursor.fetchall()]

    def set_invocation_outputs(self, cursor, invocation_id: str, outputs: List[Dict[str, Any]]) -> None:
        cursor.execute(
            f"UPDATE {INVOCATIONS_TABLE} SET outputs = ?, updated_at = ? WHERE id = ?",
            (json.dumps(outputs), utc_now(), invocation_id),
        )

    def rewrite_result(self, cursor, elixir_id: Optional[str], node_id: str, run_id: str, manifest: Dict[str, Any]) -> None:
        if not elixir_id:
            return
        results = self.results_for(cursor, elixir_id)
        current = results.get(node_id)
        if not current or current.get("run_id") != run_id:
            return
        results[node_id] = {**current, "manifest": manifest}
        cursor.execute(f"UPDATE {ELIXIRS_TABLE} SET results = ? WHERE id = ?", (json.dumps(results), elixir_id))

    def record_result(self, cursor, elixir_id: Optional[str], node_id: str, result: Dict[str, Any], descendants) -> None:
        if not elixir_id:
            return
        cursor.execute(f"SELECT results, definition FROM {ELIXIRS_TABLE} WHERE id = ?", (elixir_id,))
        row = cursor.fetchone()
        if row is None:
            return
        results = json.loads(row["results"]) if row["results"] else {}
        results[node_id] = {**result, "stale": False, "stale_reason": None}
        ran_with = (result.get("session") or {}).get("session_hash")
        if ran_with:
            node = next((item for item in json.loads(row["definition"]).get("nodes") or [] if item.get("id") == node_id), None)
            if node is not None and is_following(node) and node.get("session_hash") != ran_with:
                results[node_id].update(stale=True, stale_reason=STALE_SESSION)
        for descendant in descendants:
            current = results.get(descendant)
            if current and current.get("run_id") != result["run_id"]:
                results[descendant] = {**current, "stale": True, "stale_reason": STALE_UPSTREAM}
        cursor.execute(
            f"UPDATE {ELIXIRS_TABLE} SET results = ?, result_revision = result_revision + 1 WHERE id = ?",
            (json.dumps(results), elixir_id),
        )

    def runs_for_elixir(self, cursor, elixir_id: str) -> List[Dict[str, Any]]:
        cursor.execute(f"SELECT * FROM {RUNS_TABLE} WHERE elixir_id = ? ORDER BY created_at DESC, id DESC", (elixir_id,))
        return [_run_from_row(row) for row in cursor.fetchall()]

    def results_for(self, cursor, elixir_id: str) -> Dict[str, Any]:
        cursor.execute(f"SELECT results FROM {ELIXIRS_TABLE} WHERE id = ?", (elixir_id,))
        row = cursor.fetchone()
        return json.loads(row["results"]) if row and row["results"] else {}
