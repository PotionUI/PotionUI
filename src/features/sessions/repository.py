"""
Session Repository

Handles database operations for sessions. Returns Pydantic DTOs, not DB models.
"""
from typing import List, Optional
import uuid
import json
from datetime import datetime, timezone

from src.platform.database import get_database_connection
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
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def create(self, session: Session) -> Session:
        """Create a new session."""
        with get_database_connection() as conn:
            cursor = conn.cursor()

            # Generate ID and timestamps if not provided
            session_id = session.id if session.id else str(uuid.uuid4())
            created_at = session.created_at if session.created_at else datetime.now(timezone.utc)
            updated_at = datetime.now(timezone.utc)

            cursor.execute("""
                INSERT INTO sessions (id, user_id, preset_id, name, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id,
                session.user_id,
                session.preset_id,
                session.name,
                json.dumps(session.data),
                created_at.isoformat() if isinstance(created_at, datetime) else created_at,
                updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at
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
                updated_at=updated_at
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
                ORDER BY updated_at DESC
            """, (user_id, preset_id))

            rows = cursor.fetchall()
            return [self._row_to_session(row) for row in rows]

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

    def update(self, session: Session) -> Session:
        """Update an existing session."""
        with get_database_connection() as conn:
            cursor = conn.cursor()

            # Update timestamp
            updated_at = datetime.now(timezone.utc)

            cursor.execute("""
                UPDATE sessions
                SET name = ?, data = ?, updated_at = ?
                WHERE id = ?
            """, (
                session.name,
                json.dumps(session.data),
                updated_at.isoformat(),
                session.id
            ))

            conn.commit()

            # Return updated session
            return Session(
                id=session.id,
                user_id=session.user_id,
                preset_id=session.preset_id,
                name=session.name,
                data=session.data,
                created_at=session.created_at,
                updated_at=updated_at
            )

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
