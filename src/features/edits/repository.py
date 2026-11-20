import json
from typing import Dict, List, Optional, Sequence, Set, Tuple

from src.features.edits.records import Edit
from src.platform.database import get_cursor

SUMMARY_COLUMNS = (
    "id, user_id, tool, name, source_kind, source_id, source_name, thumbnail_upload_id, revision, created_at, updated_at, "
    "CASE WHEN tool = 'video' THEN json_extract(state, '$.clip') END AS clip"
)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class EditRepository:

    def create(self, edit: Edit, now: str) -> Edit:
        with get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO edits (id, user_id, tool, name, source_kind, source_id, source_name, state, "
                "thumbnail_upload_id, revision, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    edit.id, edit.user_id, edit.tool, edit.name, edit.source_kind, edit.source_id,
                    edit.source_name,
                    json.dumps(edit.state, separators=(",", ":")), edit.thumbnail_upload_id, now, now,
                ),
            )
        return self.get(edit.user_id, edit.id)

    def get(self, user_id: str, edit_id: str) -> Optional[Edit]:
        with get_cursor() as cursor:
            cursor.execute("SELECT * FROM edits WHERE id = ? AND user_id = ?", (edit_id, user_id))
            row = cursor.fetchone()
        if row is None:
            return None
        return Edit.from_row(row, json.loads(row["state"]))

    def existing_ids(self, user_id: str, edit_ids: Sequence[str]) -> Set[str]:
        unique = list(dict.fromkeys(edit_ids))
        if not unique:
            return set()
        found: Set[str] = set()
        with get_cursor() as cursor:
            for start in range(0, len(unique), 500):
                chunk = unique[start:start + 500]
                marks = ", ".join("?" for _ in chunk)
                cursor.execute(
                    f"SELECT id FROM edits WHERE user_id = ? AND id IN ({marks})", (user_id, *chunk)
                )
                found.update(row["id"] for row in cursor.fetchall())
        return found

    def list(
        self, user_id: str, tool: Optional[str], search: Optional[str], limit: int, offset: int
    ) -> Tuple[List[Edit], int]:
        clauses = ["user_id = ?"]
        params: list = [user_id]
        if tool:
            clauses.append("tool = ?")
            params.append(tool)
        if search:
            clauses.append(
                "(LOWER(COALESCE(name, '')) LIKE LOWER(?) ESCAPE '\\' "
                "OR LOWER(COALESCE(source_name, '')) LIKE LOWER(?) ESCAPE '\\')"
            )
            pattern = f"%{_escape_like(search)}%"
            params.extend([pattern, pattern])
        where = " AND ".join(clauses)
        with get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count FROM edits WHERE {where}", params)
            total = cursor.fetchone()["count"]
            cursor.execute(
                f"SELECT {SUMMARY_COLUMNS} FROM edits WHERE {where} "
                "ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            return [Edit.from_row(row) for row in cursor.fetchall()], total

    def update_state(
        self,
        user_id: str,
        edit_id: str,
        revision: int,
        state: Dict,
        set_thumbnail: bool,
        thumbnail_upload_id: Optional[str],
        now: str,
    ) -> Tuple[bool, Optional[int]]:
        assignments = ["state = ?", "revision = revision + 1", "updated_at = ?"]
        params: list = [json.dumps(state, separators=(",", ":")), now]
        if set_thumbnail:
            assignments.append("thumbnail_upload_id = ?")
            params.append(thumbnail_upload_id)
        with get_cursor() as cursor:
            cursor.execute(
                f"UPDATE edits SET {', '.join(assignments)} WHERE id = ? AND user_id = ? AND revision = ?",
                (*params, edit_id, user_id, revision),
            )
            applied = cursor.rowcount == 1
            cursor.execute("SELECT revision FROM edits WHERE id = ? AND user_id = ?", (edit_id, user_id))
            row = cursor.fetchone()
        return applied, (row["revision"] if row else None)

    def rename(self, user_id: str, edit_id: str, name: Optional[str], now: str) -> bool:
        with get_cursor() as cursor:
            cursor.execute(
                "UPDATE edits SET name = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (name, now, edit_id, user_id),
            )
            return cursor.rowcount == 1

    def delete(self, user_id: str, edit_id: str) -> bool:
        with get_cursor() as cursor:
            cursor.execute("DELETE FROM edits WHERE id = ? AND user_id = ?", (edit_id, user_id))
            return cursor.rowcount == 1
