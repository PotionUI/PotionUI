"""
Session Repository

Handles database operations for sessions. Returns Pydantic DTOs, not DB models.
"""
from typing import Any, Dict, List, Optional
import uuid
import json
from datetime import datetime, timezone

from src.platform.database import get_database_connection
from src.platform.database.rows import dt_column, now_utc
from src.features.sessions.dto import Session


class SessionRepository:
    """Repository for session database operations."""

    def __init__(self):
        pass

    def _row_to_session(self, row) -> Session:
        """Convert a database row to Session DTO."""
        data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']

        return Session(
            id=row['id'],
            user_id=row['user_id'],
            preset_id=row['preset_id'],
            name=row['name'],
            data=data,
            created_at=dt_column(row['created_at']) or now_utc(),
            updated_at=dt_column(row['updated_at']),
            pinned=bool(row['pinned']),
            pinned_at=dt_column(row['pinned_at']),
            version=row['version'],
            last_origin=json.loads(row['last_origin']) if row['last_origin'] else None,
        )

    def create(self, session: Session, origin: Optional[Dict[str, Any]] = None) -> Session:
        """Create a new session."""
        with get_database_connection() as conn:
            cursor = conn.cursor()

            # Generate ID and timestamps if not provided
            session_id = session.id if session.id else str(uuid.uuid4())
            created_at = session.created_at if session.created_at else datetime.now(timezone.utc)
            updated_at = datetime.now(timezone.utc)

            cursor.execute("""
                INSERT INTO sessions (id, user_id, preset_id, name, data, created_at, updated_at, version, last_origin)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (
                session_id,
                session.user_id,
                session.preset_id,
                session.name,
                json.dumps(session.data),
                created_at.isoformat() if isinstance(created_at, datetime) else created_at,
                updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at,
                json.dumps(origin) if origin else None,
            ))

            conn.commit()

            # Return updated session with generated values
            return Session(
                id=session_id,
                user_id=session.user_id,
                preset_id=session.preset_id,
                name=session.name,
                data=session.data,
                created_at=created_at,
                updated_at=updated_at,
                version=1,
                last_origin=origin,
            )

    def get_by_id(self, session_id: str) -> Optional[Session]:
        """Get session by ID."""
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cursor.fetchone()

            if row:
                return self._row_to_session(row)
            return None

    def get_by_user_and_preset(self, user_id: str, preset_id: str) -> List[Session]:
        """Get all sessions for a user and preset."""
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM sessions
                WHERE user_id = ? AND preset_id = ?
                ORDER BY pinned DESC, pinned_at DESC, updated_at DESC, id DESC
            """, (user_id, preset_id))

            rows = cursor.fetchall()
            return [self._row_to_session(row) for row in rows]

    def list_for_user(
        self, user_id: str, preset_id: Optional[str] = None, name: Optional[str] = None, query: Optional[str] = None
    ) -> List[Session]:
        clauses = ["user_id = ?"]
        params: List[Any] = [user_id]
        if preset_id:
            clauses.append("preset_id = ?")
            params.append(preset_id)
        if name:
            clauses.append("name = ?")
            params.append(name)
        if query:
            clauses.append("LOWER(name) LIKE ? ESCAPE '\\'")
            escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM sessions WHERE {' AND '.join(clauses)} "
                "ORDER BY pinned DESC, pinned_at DESC, updated_at DESC, id DESC",
                params,
            )
            return [self._row_to_session(row) for row in cursor.fetchall()]

    def get_by_user_preset_and_name(self, user_id: str, preset_id: str, name: str) -> Optional[Session]:
        """Get a specific session by user, preset, and name."""
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM sessions
                WHERE user_id = ? AND preset_id = ? AND name = ?
            """, (user_id, preset_id, name))

            row = cursor.fetchone()
            if row:
                return self._row_to_session(row)
            return None

    def update(
        self,
        session: Session,
        expected_version: int,
        origin: Optional[Dict[str, Any]] = None,
    ) -> Optional[Session]:
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE sessions
                SET name = ?, data = ?, updated_at = ?, version = version + 1, last_origin = ?
                WHERE id = ? AND version = ?
                """,
                (
                    session.name,
                    json.dumps(session.data),
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(origin) if origin else None,
                    session.id,
                    expected_version,
                ),
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None
            cursor.execute("SELECT * FROM sessions WHERE id = ?", (session.id,))
            row = cursor.fetchone()
            return self._row_to_session(row) if row else None

    def set_pinned(self, session_id: str, pinned: bool) -> Optional[Session]:
        with get_database_connection() as conn:
            cursor = conn.cursor()
            pinned_at = datetime.now(timezone.utc).isoformat() if pinned else None
            cursor.execute(
                "UPDATE sessions SET pinned = ?, pinned_at = ? WHERE id = ?",
                (1 if pinned else 0, pinned_at, session_id)
            )
            conn.commit()
            cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cursor.fetchone()
            return self._row_to_session(row) if row else None

    def delete(self, session_id: str) -> bool:
        """Delete a session."""
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0

    def exists(self, session_id: str) -> bool:
        """Check if a session exists by ID."""
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,))
            return cursor.fetchone() is not None


# Global repository instance
session_repo = SessionRepository()
