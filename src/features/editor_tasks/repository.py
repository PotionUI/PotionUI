import json
from typing import Any, Dict, List, Optional

from src.features.editor_tasks.records import EditorRun
from src.platform.database import get_cursor

_UNSET = object()


def _dump(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, separators=(",", ":"))


class EditorRunRepository:

    def create(self, run: EditorRun, now: str) -> EditorRun:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO editor_runs (id, user_id, edit_id, step_id, task, generation_id, status, rect, crop, "
                "candidates, error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id, run.user_id, run.edit_id, run.step_id, run.task, run.generation_id, run.status,
                    _dump(run.rect), _dump(run.crop), _dump(run.candidates), run.error, now, now,
                ),
            )
        return self.get(run.user_id, run.id)

    def get(self, user_id: str, run_id: str) -> Optional[EditorRun]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM editor_runs WHERE id = ? AND user_id = ?", (run_id, user_id))
            row = cursor.fetchone()
        return EditorRun.from_row(row) if row else None

    def find(self, run_id: str) -> Optional[EditorRun]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM editor_runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
        return EditorRun.from_row(row) if row else None

    def by_generation(self, generation_id: str) -> Optional[EditorRun]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM editor_runs WHERE generation_id = ?", (generation_id,))
            row = cursor.fetchone()
        return EditorRun.from_row(row) if row else None

    def update(
        self,
        run_id: str,
        now: str,
        status: Optional[str] = None,
        generation_id: Any = _UNSET,
        rect: Any = _UNSET,
        crop: Any = _UNSET,
        candidates: Any = _UNSET,
        error: Any = _UNSET,
    ) -> bool:
        sets: List[str] = ["updated_at = ?"]
        params: List[Any] = [now]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if generation_id is not _UNSET:
            sets.append("generation_id = ?")
            params.append(generation_id)
        for column, value in (("rect", rect), ("crop", crop), ("candidates", candidates)):
            if value is not _UNSET:
                sets.append(f"{column} = ?")
                params.append(_dump(value))
        if error is not _UNSET:
            sets.append("error = ?")
            params.append(error)
        params.append(run_id)
        with get_cursor() as cursor:
            cursor.execute(f"UPDATE editor_runs SET {', '.join(sets)} WHERE id = ?", params)
            return cursor.rowcount > 0

    def settle(self, run_id: str, now: str, status: str, candidates: Optional[List[Dict[str, Any]]], error: Optional[str]) -> bool:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE editor_runs SET status = ?, candidates = ?, error = ?, updated_at = ? "
                "WHERE id = ? AND status IN ('queued', 'running')",
                (status, _dump(candidates) if candidates is not None else None, error, now, run_id),
            )
            return cursor.rowcount > 0

    def referenced(self, upload_id: str, exclude: Optional[str]) -> bool:
        with get_cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM editor_runs WHERE id != ? AND status IN ('queued', 'running') AND crop LIKE ? LIMIT 1",
                (exclude or "", f'%"{upload_id}"%'),
            )
            return cursor.fetchone() is not None

    def delete(self, run_id: str) -> bool:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM editor_runs WHERE id = ?", (run_id,))
            return cursor.rowcount > 0
