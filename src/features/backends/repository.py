from typing import Any, Dict, List, Optional
from src.platform.database import db
from src.features.backends.records import Backend
from src.platform.security.secrets import get_secret_cipher
from src.platform.util.ids import generate_ulid

class BackendRepository:
    @staticmethod
    def _decrypt(backend: Backend) -> Backend:
        """Unwrap every encryption envelope in a backend's config.

        Driven by the envelope marker rather than by the engine's schema, so a
        reader that never resolves a config class still gets usable values.
        """
        cipher = get_secret_cipher()
        for key, value in backend.config.items():
            if cipher.is_encrypted(value):
                backend.config[key] = cipher.decrypt(
                    value, context=f"backends:{backend.id}/{key}"
                )
        return backend

    def get_all(self) -> List[Backend]:
        """Get all backends"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM backends ORDER BY name")
            return [self._decrypt(Backend.from_row(row)) for row in cursor.fetchall()]

    def get_by_id(self, backend_id: str) -> Optional[Backend]:
        """Get backend by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM backends WHERE id = ?", (backend_id,))
            row = cursor.fetchone()
            return self._decrypt(Backend.from_row(row)) if row else None

    def get_by_engine(self, engine: str) -> List[Backend]:
        """Get all backends providing a given engine"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM backends WHERE engine = ? ORDER BY name", (engine,))
            return [self._decrypt(Backend.from_row(row)) for row in cursor.fetchall()]

    def get_default(self, engine: str) -> Optional[Backend]:
        """Get the default backend for an engine"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM backends WHERE engine = ? AND is_default = 1 LIMIT 1",
                (engine,)
            )
            row = cursor.fetchone()
            return self._decrypt(Backend.from_row(row)) if row else None

    def create(self, backend: Backend) -> Backend:
        """Create a new backend"""
        if not backend.id:
            backend.id = generate_ulid()

        if backend.is_default:
            self._unset_defaults_for_engine(backend.engine)

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO backends (id, name, engine, driver, enabled, is_default, config, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                backend.id,
                backend.name,
                backend.engine,
                backend.driver,
                backend.enabled,
                backend.is_default,
                backend.serialize_config(),
                backend.description
            ))

        return self.get_by_id(backend.id)

    def update(self, backend_id: str, backend: Backend) -> Optional[Backend]:
        """Update an existing backend"""
        if backend.is_default:
            self._unset_defaults_for_engine(backend.engine, exclude_id=backend_id)

        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE backends
                SET name = ?, engine = ?, driver = ?, enabled = ?, is_default = ?, config = ?, description = ?
                WHERE id = ?
            """, (
                backend.name,
                backend.engine,
                backend.driver,
                backend.enabled,
                backend.is_default,
                backend.serialize_config(),
                backend.description,
                backend_id
            ))

            if cursor.rowcount == 0:
                return None

        return self.get_by_id(backend_id)

    def delete(self, backend_id: str) -> bool:
        """Delete backend by ID"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM backends WHERE id = ?", (backend_id,))
            return cursor.rowcount > 0

    def set_default(self, backend_id: str, engine: str) -> bool:
        """Set a backend as the default for its engine"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE backends SET is_default = 0 WHERE engine = ?",
                (engine,)
            )
            cursor.execute(
                "UPDATE backends SET is_default = 1 WHERE id = ? AND engine = ?",
                (backend_id, engine)
            )
            return cursor.rowcount > 0

    def iter_encrypted_configs(self) -> List[Dict[str, Any]]:
        """Raw (undecrypted) config blobs, for the preflight check and rotation."""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT id, config FROM backends")
            return [dict(row) for row in cursor.fetchall()]

    def replace_config(self, backend_id: str, serialized_config: str) -> None:
        """Overwrite one backend's stored config JSON verbatim."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE backends SET config = ? WHERE id = ?",
                (serialized_config, backend_id),
            )

    def _unset_defaults_for_engine(self, engine: str, exclude_id: Optional[str] = None):
        """Unset the default flag from all backends of an engine"""
        with db.get_cursor() as cursor:
            if exclude_id:
                cursor.execute(
                    "UPDATE backends SET is_default = 0 WHERE engine = ? AND id != ?",
                    (engine, exclude_id)
                )
            else:
                cursor.execute("UPDATE backends SET is_default = 0 WHERE engine = ?", (engine,))

# Global repository instance
backend_repo = BackendRepository()
