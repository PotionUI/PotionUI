import json
from typing import Any, Dict, Iterable, Optional, Set


class GenerationRunReportRepository:
    """Durable store for `RunReportRecorder.flush()` output (migration 117)."""

    def save(self, generation_id: str, report: Dict[str, Any]) -> None:
        """Upsert the report for a generation.

        A generation reaches a terminal state exactly once in normal
        operation, but `INSERT OR REPLACE` makes a duplicate flush (e.g. a
        retried completion signal) overwrite rather than raise on the
        primary-key collision.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO generation_run_reports (generation_id, report)
                VALUES (?, ?)
                """,
                (generation_id, json.dumps(report)),
            )

    def get(self, generation_id: str) -> Optional[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT report FROM generation_run_reports WHERE generation_id = ?",
                (generation_id,),
            )
            row = cursor.fetchone()
            return json.loads(row["report"]) if row else None

    def exists_bulk(self, generation_ids: Iterable[str]) -> Set[str]:
        """Which of `generation_ids` have a persisted run report.

        Bulk rather than one query per row, matching
        `FileRepository.get_generation_files_bulk`.
        """
        ids = list(generation_ids)
        if not ids:
            return set()
        placeholders = ",".join("?" * len(ids))
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT generation_id FROM generation_run_reports "
                f"WHERE generation_id IN ({placeholders})",
                ids,
            )
            return {row["generation_id"] for row in cursor.fetchall()}
