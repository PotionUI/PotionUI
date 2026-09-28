from typing import Any, Dict, List, Optional, Set

_ROOT_COLUMNS = (
    "id, label, path, path_key, kind, read_only, case_insensitive, "
    "state, state_reason, state_checked_at, created_at, updated_at"
)
_BINDING_COLUMNS = "root_id, model_type, subdir, position, is_write"


class ModelRootRepository:

    def list_roots(self) -> List[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT {_ROOT_COLUMNS} FROM model_roots ORDER BY created_at")
            return [dict(row) for row in cursor.fetchall()]

    def get_root(self, root_id: str) -> Optional[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT {_ROOT_COLUMNS} FROM model_roots WHERE id = ?", (root_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_root_by_path_key(self, path_key: str) -> Optional[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT {_ROOT_COLUMNS} FROM model_roots WHERE path_key = ?", (path_key,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_bindings(self, model_type: Optional[str] = None) -> List[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if model_type is None:
                cursor.execute(
                    f"SELECT {_BINDING_COLUMNS} FROM model_root_bindings ORDER BY model_type, position"
                )
            else:
                cursor.execute(
                    f"SELECT {_BINDING_COLUMNS} FROM model_root_bindings "
                    "WHERE model_type = ? ORDER BY position",
                    (model_type,),
                )
            return [dict(row) for row in cursor.fetchall()]

    def has_binding(self, root_id: str, model_type: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM model_root_bindings WHERE root_id = ? AND model_type = ?",
                (root_id, model_type),
            )
            return cursor.fetchone() is not None

    def max_position_by_type(self) -> Dict[str, int]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT model_type, MAX(position) AS max_position FROM model_root_bindings GROUP BY model_type"
            )
            return {row["model_type"]: row["max_position"] for row in cursor.fetchall()}

    def write_bound_types(self) -> Set[str]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT model_type FROM model_root_bindings WHERE is_write = 1")
            return {row["model_type"] for row in cursor.fetchall()}

    def ensure_root(
        self,
        root_id: str,
        label: str,
        path: str,
        path_key: str,
        kind: str,
        read_only: bool,
        case_insensitive: bool,
        state: str,
        now: str,
    ) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT OR IGNORE INTO model_roots
                    (id, label, path, path_key, kind, read_only, case_insensitive, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    root_id,
                    label,
                    path,
                    path_key,
                    kind,
                    int(read_only),
                    int(case_insensitive),
                    state,
                    now,
                    now,
                ),
            )

    def insert_binding(
        self,
        root_id: str,
        model_type: str,
        subdir: str,
        position: int,
        is_write: bool,
    ) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT OR IGNORE INTO model_root_bindings (root_id, model_type, subdir, position, is_write)
                VALUES (?, ?, ?, ?, ?)
                """,
                (root_id, model_type, subdir, position, int(is_write)),
            )

    def update_root_state(self, root_id: str, state: str, state_reason: Optional[str], checked_at: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE model_roots SET state = ?, state_reason = ?, state_checked_at = ?, updated_at = ? "
                "WHERE id = ?",
                (state, state_reason, checked_at, checked_at, root_id),
            )

    def insert_root(
        self,
        root_id: str,
        label: str,
        path: str,
        path_key: str,
        kind: str,
        read_only: bool,
        case_insensitive: bool,
        now: str,
    ) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO model_roots
                    (id, label, path, path_key, kind, read_only, case_insensitive, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'online', ?, ?)
                """,
                (root_id, label, path, path_key, kind, int(read_only), int(case_insensitive), now, now),
            )

    def update_root(
        self,
        root_id: str,
        *,
        label: Optional[str] = None,
        path: Optional[str] = None,
        path_key: Optional[str] = None,
        read_only: Optional[bool] = None,
        now: str,
    ) -> None:
        from src.platform.database.database import db
        fields = []
        values: List[Any] = []
        if label is not None:
            fields.append("label = ?")
            values.append(label)
        if path is not None:
            fields.append("path = ?")
            values.append(path)
        if path_key is not None:
            fields.append("path_key = ?")
            values.append(path_key)
        if read_only is not None:
            fields.append("read_only = ?")
            values.append(int(read_only))
        if not fields:
            return
        fields.append("updated_at = ?")
        values.append(now)
        values.append(root_id)
        with db.get_cursor() as cursor:
            cursor.execute(f"UPDATE model_roots SET {', '.join(fields)} WHERE id = ?", tuple(values))

    def delete_root(self, root_id: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM model_roots WHERE id = ?", (root_id,))

    def upsert_binding(self, root_id: str, model_type: str, subdir: str, position: int, is_write: bool = False) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM model_root_bindings WHERE root_id = ? AND model_type = ?",
                (root_id, model_type),
            )
            if cursor.fetchone() is not None:
                cursor.execute(
                    "UPDATE model_root_bindings SET subdir = ? WHERE root_id = ? AND model_type = ?",
                    (subdir, root_id, model_type),
                )
                return
            cursor.execute(
                """
                INSERT INTO model_root_bindings (root_id, model_type, subdir, position, is_write)
                VALUES (?, ?, ?, ?, ?)
                """,
                (root_id, model_type, subdir, position, int(is_write)),
            )

    def clear_write(self, model_type: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE model_root_bindings SET is_write = 0 WHERE model_type = ? AND is_write = 1",
                (model_type,),
            )

    def set_write(self, root_id: str, model_type: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE model_root_bindings SET is_write = 0 WHERE model_type = ? AND is_write = 1",
                (model_type,),
            )
            cursor.execute(
                "UPDATE model_root_bindings SET is_write = 1 WHERE root_id = ? AND model_type = ?",
                (root_id, model_type),
            )

    def clear_write_for_root(self, root_id: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE model_root_bindings SET is_write = 0 WHERE root_id = ? AND is_write = 1",
                (root_id,),
            )

    def reorder_bindings(self, model_type: str, ordered_root_ids: List[str]) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            for index, root_id in enumerate(ordered_root_ids):
                cursor.execute(
                    "UPDATE model_root_bindings SET position = ? WHERE root_id = ? AND model_type = ?",
                    (-(index + 1), root_id, model_type),
                )
            for index, root_id in enumerate(ordered_root_ids):
                cursor.execute(
                    "UPDATE model_root_bindings SET position = ? WHERE root_id = ? AND model_type = ?",
                    (index, root_id, model_type),
                )

    def delete_binding(self, root_id: str, model_type: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM model_root_bindings WHERE root_id = ? AND model_type = ?",
                (root_id, model_type),
            )

    def bindings_for_root(self, root_id: str) -> List[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT {_BINDING_COLUMNS} FROM model_root_bindings WHERE root_id = ? ORDER BY model_type",
                (root_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
