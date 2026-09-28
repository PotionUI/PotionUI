from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.platform.util.ids import generate_ulid

_LOCATION_COLUMNS = (
    "id, model_id, root_id, model_type, rel_path, rel_key, size, mtime_ns, sha256, status, seen_at"
)


def _pick_winner(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    return min(rows, key=lambda row: (row["position"], len(row["rel_path"]), row["rel_path"]))


class ModelLocationsRepository:

    def get(self, root_id: str, model_type: str, rel_key: str) -> Optional[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT {_LOCATION_COLUMNS} FROM model_locations "
                "WHERE root_id = ? AND model_type = ? AND rel_key = ?",
                (root_id, model_type, rel_key),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_for_root_type(self, root_id: str, model_type: str) -> List[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT {_LOCATION_COLUMNS} FROM model_locations WHERE root_id = ? AND model_type = ?",
                (root_id, model_type),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_for_model(self, model_id: str) -> List[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT {_LOCATION_COLUMNS} FROM model_locations WHERE model_id = ?",
                (model_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def upsert(
        self,
        *,
        model_id: str,
        root_id: str,
        model_type: str,
        rel_path: str,
        rel_key: str,
        size: Optional[int],
        mtime_ns: Optional[int],
        sha256: Optional[str],
        status: str,
        seen_at: str,
    ) -> str:
        location_id = generate_ulid()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO model_locations
                    (id, model_id, root_id, model_type, rel_path, rel_key, size, mtime_ns, sha256, status, seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(root_id, model_type, rel_key) DO UPDATE SET
                    model_id = excluded.model_id,
                    rel_path = excluded.rel_path,
                    size = excluded.size,
                    mtime_ns = excluded.mtime_ns,
                    sha256 = excluded.sha256,
                    status = excluded.status,
                    seen_at = excluded.seen_at
                """,
                (
                    location_id, model_id, root_id, model_type, rel_path, rel_key,
                    size, mtime_ns, sha256, status, seen_at,
                ),
            )
            cursor.execute(
                "SELECT id FROM model_locations WHERE root_id = ? AND model_type = ? AND rel_key = ?",
                (root_id, model_type, rel_key),
            )
            row = cursor.fetchone()
            return row["id"] if row else location_id

    def touch_present(self, root_id: str, model_type: str, rel_key: str, seen_at: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE model_locations SET status = 'present', seen_at = ? "
                "WHERE root_id = ? AND model_type = ? AND rel_key = ?",
                (seen_at, root_id, model_type, rel_key),
            )

    def adopt_mtime(self, root_id: str, model_type: str, rel_key: str, mtime_ns: int, seen_at: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE model_locations SET status = 'present', mtime_ns = ?, seen_at = ? "
                "WHERE root_id = ? AND model_type = ? AND rel_key = ?",
                (mtime_ns, seen_at, root_id, model_type, rel_key),
            )

    def mark_missing_for_root_type(
        self, root_id: str, model_type: str, present_rel_keys: Sequence[str], seen_at: str
    ) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if present_rel_keys:
                placeholders = ",".join("?" for _ in present_rel_keys)
                cursor.execute(
                    f"UPDATE model_locations SET status = 'missing', seen_at = ? "
                    f"WHERE root_id = ? AND model_type = ? AND status != 'missing' "
                    f"AND rel_key NOT IN ({placeholders})",
                    (seen_at, root_id, model_type, *present_rel_keys),
                )
            else:
                cursor.execute(
                    "UPDATE model_locations SET status = 'missing', seen_at = ? "
                    "WHERE root_id = ? AND model_type = ? AND status != 'missing'",
                    (seen_at, root_id, model_type),
                )
            return cursor.rowcount

    def delete_missing_for_roots(self, root_ids: Sequence[str]) -> int:
        if not root_ids:
            return 0
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            placeholders = ",".join("?" for _ in root_ids)
            cursor.execute(
                f"DELETE FROM model_locations WHERE status = 'missing' AND root_id IN ({placeholders})",
                tuple(root_ids),
            )
            return cursor.rowcount

    def delete(self, location_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM model_locations WHERE id = ?", (location_id,))
            return cursor.rowcount > 0

    def list_present_for_roots(self, root_ids: Sequence[str]) -> List[Dict[str, Any]]:
        if not root_ids:
            return []
        from src.platform.database.database import db
        placeholders = ",".join("?" for _ in root_ids)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT ml.id, ml.model_id, ml.root_id, ml.model_type, ml.rel_path, ml.rel_key,
                       ml.size, ml.mtime_ns, ml.sha256, ml.status,
                       mrb.position AS position, mrb.subdir AS subdir
                FROM model_locations ml
                JOIN model_root_bindings mrb
                    ON mrb.root_id = ml.root_id AND mrb.model_type = ml.model_type
                WHERE ml.status = 'present' AND ml.root_id IN ({placeholders})
                """,
                tuple(root_ids),
            )
            return [dict(row) for row in cursor.fetchall()]

    def winners_by_model(self, root_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        by_model: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.list_present_for_roots(root_ids):
            by_model.setdefault(row["model_id"], []).append(row)
        return {model_id: _pick_winner(rows) for model_id, rows in by_model.items()}

    def list_conflicts(self, limit: int = 200) -> List[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT ml.id, ml.model_id, ml.root_id, ml.model_type, ml.rel_path, ml.rel_key, "
                "ml.size, ml.mtime_ns, ml.sha256, ml.status, ml.seen_at, mr.label AS root_label "
                "FROM model_locations ml JOIN model_roots mr ON mr.id = ml.root_id "
                "WHERE ml.status = 'conflict' ORDER BY ml.seen_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_duplicate_models(self, limit: int = 200) -> List[Dict[str, Any]]:
        from pathlib import PurePosixPath

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT ml.model_id, ml.model_type, ml.root_id, ml.rel_path, "
                "mr.label AS root_label, mrb.position AS position "
                "FROM model_locations ml "
                "JOIN model_roots mr ON mr.id = ml.root_id "
                "JOIN model_root_bindings mrb ON mrb.root_id = ml.root_id AND mrb.model_type = ml.model_type "
                "WHERE ml.status = 'present' AND ml.model_id IN ("
                "  SELECT model_id FROM model_locations WHERE status = 'present' "
                "  GROUP BY model_id HAVING COUNT(*) > 1"
                ") ORDER BY ml.model_id, mrb.position",
                (),
            )
            rows = [dict(row) for row in cursor.fetchall()]

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["model_id"], []).append(row)

        entries = []
        for group in grouped.values():
            winner = _pick_winner(group)
            filename = PurePosixPath(winner["rel_path"]).name
            entries.append({
                "model_type": group[0]["model_type"],
                "filename": filename,
                "copies": [
                    {
                        "root_label": row["root_label"],
                        "rel_path": row["rel_path"],
                        "winner": row is winner,
                    }
                    for row in group
                ],
            })

        entries.sort(key=lambda entry: len(entry["copies"]), reverse=True)
        return entries[:limit]

    def aggregate_by_root_and_type(self) -> Dict[Tuple[str, str], Dict[str, int]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT root_id, model_type, COUNT(*) AS indexed_files, COALESCE(SUM(size), 0) AS size_bytes
                FROM model_locations
                WHERE status = 'present'
                GROUP BY root_id, model_type
                """
            )
            return {
                (row["root_id"], row["model_type"]): {
                    "indexed_files": row["indexed_files"],
                    "size_bytes": row["size_bytes"],
                }
                for row in cursor.fetchall()
            }

    def count_for_root(self, root_id: str) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM model_locations WHERE root_id = ?", (root_id,))
            row = cursor.fetchone()
            return row["n"] if row else 0

    def delete_for_root_and_type(self, root_id: str, model_type: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM model_locations WHERE root_id = ? AND model_type = ?",
                (root_id, model_type),
            )

    def rehome(self, src_root_id: str, model_type: str, dst_root_id: str, dst_rel_prefix: str = "") -> int:
        from pathlib import PurePosixPath

        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT {_LOCATION_COLUMNS} FROM model_locations WHERE root_id = ? AND model_type = ?",
                (src_root_id, model_type),
            )
            rows = [dict(row) for row in cursor.fetchall()]

            moved = 0
            for row in rows:
                if dst_rel_prefix:
                    new_rel_path = str(PurePosixPath(dst_rel_prefix) / row["rel_path"])
                    new_rel_key = str(PurePosixPath(dst_rel_prefix) / row["rel_key"])
                else:
                    new_rel_path = row["rel_path"]
                    new_rel_key = row["rel_key"]

                cursor.execute(
                    "SELECT id FROM model_locations WHERE root_id = ? AND model_type = ? AND rel_key = ?",
                    (dst_root_id, model_type, new_rel_key),
                )
                conflict = cursor.fetchone()
                if conflict is not None:
                    cursor.execute("DELETE FROM model_locations WHERE id = ?", (row["id"],))
                else:
                    cursor.execute(
                        "UPDATE model_locations SET root_id = ?, rel_path = ?, rel_key = ?, mtime_ns = NULL WHERE id = ?",
                        (dst_root_id, new_rel_path, new_rel_key, row["id"]),
                    )
                moved += 1
            return moved
