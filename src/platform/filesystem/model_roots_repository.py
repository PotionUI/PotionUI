from typing import Any, Dict, List, Optional, Set

from src.platform.filesystem.model_roots import binding_subdir_key
from src.platform.filesystem.model_types import binding_scans_headers_by_default
from src.platform.util.ids import generate_ulid

_ROOT_COLUMNS = (
    "id, label, path, path_key, kind, read_only, case_insensitive, "
    "state, state_reason, state_checked_at, created_at, updated_at, layout_profile"
)
_BINDING_COLUMNS = "id, root_id, model_type, subdir, subdir_key, position, is_write, scan_headers"


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
        scan_headers: Optional[bool] = None,
    ) -> str:
        from src.platform.database.database import db
        if scan_headers is None:
            scan_headers = binding_scans_headers_by_default(model_type, subdir)
        binding_id = generate_ulid()
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO model_root_bindings "
                "(id, root_id, model_type, subdir, subdir_key, position, is_write, scan_headers) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    binding_id, root_id, model_type, subdir,
                    self._subdir_key(cursor, root_id, subdir), position, int(is_write), int(scan_headers),
                ),
            )
        return binding_id

    @staticmethod
    def _subdir_key(cursor, root_id: str, subdir: str) -> str:
        cursor.execute("SELECT case_insensitive FROM model_roots WHERE id = ?", (root_id,))
        row = cursor.fetchone()
        return binding_subdir_key(subdir, case_insensitive=bool(row["case_insensitive"]) if row else False)

    def bindings_for(self, root_id: str, model_type: str) -> List[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT {_BINDING_COLUMNS} FROM model_root_bindings "
                "WHERE root_id = ? AND model_type = ? ORDER BY position",
                (root_id, model_type),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_binding(self, binding_id: str) -> Optional[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT {_BINDING_COLUMNS} FROM model_root_bindings WHERE id = ?", (binding_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def find_binding(self, root_id: str, model_type: str, subdir: str) -> Optional[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            return self._find_binding(cursor, root_id, model_type, subdir)

    def _find_binding(self, cursor, root_id: str, model_type: str, subdir: str) -> Optional[Dict[str, Any]]:
        cursor.execute(
            f"SELECT {_BINDING_COLUMNS} FROM model_root_bindings "
            "WHERE root_id = ? AND model_type = ? AND subdir_key = ?",
            (root_id, model_type, self._subdir_key(cursor, root_id, subdir)),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

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
        layout_profile: Optional[str] = None,
    ) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO model_roots "
                "(id, label, path, path_key, kind, read_only, case_insensitive, state, created_at, updated_at, "
                "layout_profile) VALUES (?, ?, ?, ?, ?, ?, ?, 'online', ?, ?, ?)",
                (root_id, label, path, path_key, kind, int(read_only), int(case_insensitive), now, now, layout_profile),
            )

    def update_root(
        self,
        root_id: str,
        *,
        label: Optional[str] = None,
        path: Optional[str] = None,
        path_key: Optional[str] = None,
        read_only: Optional[bool] = None,
        layout_profile: Optional[str] = None,
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
        if layout_profile is not None:
            fields.append("layout_profile = ?")
            values.append(layout_profile)
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

    def upsert_binding(
        self,
        root_id: str,
        model_type: str,
        subdir: str,
        position: int,
        is_write: bool = False,
        scan_headers: Optional[bool] = None,
    ) -> str:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            existing = self._find_binding(cursor, root_id, model_type, subdir)
            if existing is not None:
                if scan_headers is not None:
                    cursor.execute(
                        "UPDATE model_root_bindings SET scan_headers = ? WHERE id = ?",
                        (int(scan_headers), existing["id"]),
                    )
                return existing["id"]
        return self.insert_binding(root_id, model_type, subdir, position, is_write, scan_headers)

    def set_scan_headers(
        self, root_id: str, model_type: str, enabled: bool, subdir: Optional[str] = None
    ) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if subdir is None:
                cursor.execute(
                    "UPDATE model_root_bindings SET scan_headers = ? WHERE root_id = ? AND model_type = ?",
                    (int(enabled), root_id, model_type),
                )
            else:
                cursor.execute(
                    "UPDATE model_root_bindings SET scan_headers = ? "
                    "WHERE root_id = ? AND model_type = ? AND subdir_key = ?",
                    (int(enabled), root_id, model_type, self._subdir_key(cursor, root_id, subdir)),
                )
            return cursor.rowcount > 0

    def clear_write(self, model_type: str) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE model_root_bindings SET is_write = 0 WHERE model_type = ? AND is_write = 1",
                (model_type,),
            )

    def set_write(self, root_id: str, model_type: str, subdir: Optional[str] = None) -> str:
        from src.platform.database.database import db
        from src.platform.filesystem.model_roots import BindingNotFoundError
        with db.get_cursor() as cursor:
            if subdir is None:
                cursor.execute(
                    "SELECT id FROM model_root_bindings WHERE root_id = ? AND model_type = ? "
                    "ORDER BY position LIMIT 1",
                    (root_id, model_type),
                )
                row = cursor.fetchone()
            else:
                row = self._find_binding(cursor, root_id, model_type, subdir)
            if row is None:
                raise BindingNotFoundError(root_id, model_type)
            binding_id = row["id"]
            cursor.execute(
                "UPDATE model_root_bindings SET is_write = 0 WHERE model_type = ? AND is_write = 1",
                (model_type,),
            )
            cursor.execute("UPDATE model_root_bindings SET is_write = 1 WHERE id = ?", (binding_id,))
            return binding_id

    def reorder_bindings(self, model_type: str, ordered_binding_ids: List[str]) -> None:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            for index, binding_id in enumerate(ordered_binding_ids):
                cursor.execute(
                    "UPDATE model_root_bindings SET position = ? WHERE id = ? AND model_type = ?",
                    (-(index + 1), binding_id, model_type),
                )
            for index, binding_id in enumerate(ordered_binding_ids):
                cursor.execute(
                    "UPDATE model_root_bindings SET position = ? WHERE id = ? AND model_type = ?",
                    (index, binding_id, model_type),
                )

    def delete_binding(self, root_id: str, model_type: str, subdir: Optional[str] = None) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if subdir is None:
                cursor.execute(
                    "DELETE FROM model_root_bindings WHERE root_id = ? AND model_type = ?",
                    (root_id, model_type),
                )
            else:
                cursor.execute(
                    "DELETE FROM model_root_bindings WHERE root_id = ? AND model_type = ? AND subdir_key = ?",
                    (root_id, model_type, self._subdir_key(cursor, root_id, subdir)),
                )
            return cursor.rowcount

    def bindings_for_root(self, root_id: str) -> List[Dict[str, Any]]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT {_BINDING_COLUMNS} FROM model_root_bindings WHERE root_id = ? "
                "ORDER BY model_type, position",
                (root_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
