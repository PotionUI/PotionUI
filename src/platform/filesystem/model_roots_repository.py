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
