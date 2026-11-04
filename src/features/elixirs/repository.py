import json
from typing import Any, Callable, Dict, List, Optional

from src.features.elixirs.records import (
    ACTIVE_RUN_STATUSES,
    ELIXIRS_TABLE,
    INVOCATIONS_TABLE,
    RUNS_TABLE,
    ElixirHasActiveRuns,
    ElixirRecord,
    RevisionConflict,
    utc_now,
)
from src.platform.util.ids import generate_ulid


def _placeholders(values) -> str:
    return ", ".join("?" for _ in values)


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


class ElixirRepository:
    def create(self, user_id: str, name: str, run_mode: str, definition: Dict[str, Any]) -> ElixirRecord:
        from src.platform.database.database import db

        elixir_id = generate_ulid()
        now = utc_now()
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {ELIXIRS_TABLE}
                    (id, user_id, name, run_mode, revision, definition, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (elixir_id, user_id, name, run_mode, json.dumps(definition), now, now),
            )
        return self.get(elixir_id, user_id)

    def get(self, elixir_id: str, user_id: str) -> Optional[ElixirRecord]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {ELIXIRS_TABLE} WHERE id = ? AND user_id = ?",
                (elixir_id, user_id),
            )
            row = cursor.fetchone()
        return ElixirRecord.from_row(row) if row else None

    def list(self, user_id: str) -> List[ElixirRecord]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {ELIXIRS_TABLE} WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
                (user_id,),
            )
            rows = cursor.fetchall()
        return [ElixirRecord.from_row(row) for row in rows]

    def update(
        self,
        elixir_id: str,
        user_id: str,
        expected_revision: int,
        name: Optional[str] = None,
        run_mode: Optional[str] = None,
        definition: Optional[Dict[str, Any]] = None,
    ) -> Optional[ElixirRecord]:
        from src.platform.database.database import db

        fields: Dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if run_mode is not None:
            fields["run_mode"] = run_mode
        if definition is not None:
            fields["definition"] = json.dumps(definition)
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in fields)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {ELIXIRS_TABLE} SET {assignments}, revision = revision + 1
                WHERE id = ? AND user_id = ? AND revision = ?
                """,
                (*fields.values(), elixir_id, user_id, expected_revision),
            )
            changed = cursor.rowcount
        if changed:
            return self.get(elixir_id, user_id)
        current = self.get(elixir_id, user_id)
        if current is None:
            return None
        raise RevisionConflict(current.revision)

    def delete(self, elixir_id: str, user_id: str) -> bool:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                f"SELECT 1 FROM {ELIXIRS_TABLE} WHERE id = ? AND user_id = ?", (elixir_id, user_id)
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                f"SELECT 1 FROM {RUNS_TABLE} WHERE elixir_id = ? AND status IN ({_placeholders(ACTIVE_RUN_STATUSES)}) LIMIT 1",
                (elixir_id, *ACTIVE_RUN_STATUSES),
            )
            if cursor.fetchone() is not None:
                raise ElixirHasActiveRuns(elixir_id)
            cursor.execute(
                f"DELETE FROM {ELIXIRS_TABLE} WHERE id = ? AND user_id = ?", (elixir_id, user_id)
            )
        return True

    @staticmethod
    def _mentioning(cursor, user_id: str, needle: str) -> List[Dict[str, Any]]:
        cursor.execute(
            f"SELECT id, definition, results FROM {ELIXIRS_TABLE} WHERE user_id = ? AND instr(definition, ?) > 0",
            (user_id, needle),
        )
        return [
            {"id": row["id"], "definition": json.loads(row["definition"]), "results": json.loads(row["results"] or "{}")}
            for row in cursor.fetchall()
        ]

    def mentioning(self, user_id: str, needle: str) -> List[Dict[str, Any]]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            return self._mentioning(cursor, user_id, needle)

    def rewrite_mentioning(
        self, user_id: str, needle: str, rewrite: Callable[[Dict[str, Any]], Optional[bool]]
    ) -> List[str]:
        from src.platform.database.database import db

        touched = []
        with db.get_cursor() as cursor:
            cursor.execute("BEGIN IMMEDIATE")
            for elixir in self._mentioning(cursor, user_id, needle):
                results_changed = rewrite(elixir)
                if results_changed is None:
                    continue
                cursor.execute(
                    f"UPDATE {ELIXIRS_TABLE} SET definition = ?, results = ?, result_revision = result_revision + ? WHERE id = ?",
                    (json.dumps(elixir["definition"]), json.dumps(elixir["results"]), 1 if results_changed else 0, elixir["id"]),
                )
                touched.append(elixir["id"])
        return touched

    def latest_runs(self, user_id: str, elixir_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        from src.platform.database.database import db

        if not elixir_ids:
            return {}
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, elixir_id, status, node_states, checkpoints, error, created_at, updated_at, finished_at FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY elixir_id ORDER BY created_at DESC, id DESC) AS rn
                    FROM {RUNS_TABLE}
                    WHERE user_id = ? AND elixir_id IN ({_placeholders(elixir_ids)})
                ) WHERE rn = 1
                """,
                (user_id, *elixir_ids),
            )
            rows = cursor.fetchall()
        runs = {}
        for row in rows:
            run = dict(row)
            run["node_states"] = _loads(run["node_states"], {})
            run["checkpoints"] = _loads(run["checkpoints"], {})
            runs[run["elixir_id"]] = run
        return runs

    def recent_completed_outputs(self, user_id: str, elixir_ids: List[str], per_elixir: int) -> List[Dict[str, Any]]:
        from src.platform.database.database import db

        if not elixir_ids:
            return []
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT elixir_id, run_id, node_id, outputs FROM (
                    SELECT r.elixir_id AS elixir_id, i.run_id AS run_id, i.node_id AS node_id, i.outputs AS outputs,
                           ROW_NUMBER() OVER (PARTITION BY r.elixir_id ORDER BY i.finished_at DESC, i.id DESC) AS rn
                    FROM {INVOCATIONS_TABLE} i JOIN {RUNS_TABLE} r ON r.id = i.run_id
                    WHERE r.user_id = ? AND r.elixir_id IN ({_placeholders(elixir_ids)})
                      AND i.status = 'completed' AND i.outputs <> '[]'
                ) WHERE rn <= ? ORDER BY elixir_id, rn
                """,
                (user_id, *elixir_ids, per_elixir),
            )
            return [
                {"elixir_id": row["elixir_id"], "run_id": row["run_id"], "node_id": row["node_id"], "outputs": _loads(row["outputs"], [])}
                for row in cursor.fetchall()
            ]

    def output_totals(self, run_ids: List[str]) -> Dict[tuple, int]:
        from src.platform.database.database import db

        if not run_ids:
            return {}
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT run_id, node_id, SUM(json_array_length(outputs)) AS total FROM {INVOCATIONS_TABLE}
                WHERE run_id IN ({_placeholders(run_ids)}) AND status = 'completed' GROUP BY run_id, node_id
                """,
                run_ids,
            )
            return {(row["run_id"], row["node_id"]): int(row["total"] or 0) for row in cursor.fetchall()}
