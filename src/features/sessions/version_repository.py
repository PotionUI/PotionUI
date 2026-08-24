"""
Session Version Repository

Handles database operations for session_versions. See migration 092 for the
schema. Each row is an immutable snapshot of a session's `data` at save time;
the `sessions` table itself stays the "current" state.
"""
from typing import List, Optional
import json
import uuid
from datetime import datetime, timezone

from src.platform.database import get_database_connection
from src.features.sessions.dto import SessionVersion

# Maximum historical versions retained per session. The oldest version beyond
# this cap is pruned every time a new one is inserted.
SESSION_VERSION_RETENTION_LIMIT = 50


class SessionVersionRepository:
    """Repository for session_versions database operations."""

    def __init__(self):
        pass

    def _row_to_version(self, row) -> SessionVersion:
        payload = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
        return SessionVersion(
            id=row['id'],
            session_id=row['session_id'],
            version_number=row['version_number'],
            data=payload,
            summary=row['summary'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.now(timezone.utc),
        )

    def get_latest(self, session_id: str) -> Optional[SessionVersion]:
        """Get the most recently written version for a session, or None."""
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM session_versions
                WHERE session_id = ?
                ORDER BY version_number DESC
                LIMIT 1
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            return self._row_to_version(row) if row else None

    def create(self, session_id: str, data: dict, summary: Optional[str]) -> SessionVersion:
        """
        Append a new immutable version for a session.

        Computes the next monotonic `version_number` from the current max,
        inserts the snapshot, then prunes anything beyond
        `SESSION_VERSION_RETENTION_LIMIT` (oldest first).
        """
        with get_database_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM session_versions WHERE session_id = ?",
                (session_id,),
            )
            next_version = cursor.fetchone()[0] + 1

            version_id = str(uuid.uuid4())
            created_at = datetime.now(timezone.utc)

            cursor.execute(
                """
                INSERT INTO session_versions (id, session_id, version_number, payload, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    session_id,
                    next_version,
                    json.dumps(data),
                    summary,
                    created_at.isoformat(),
                ),
            )

            # Prune everything beyond the retention cap, oldest first.
            cursor.execute(
                """
                DELETE FROM session_versions
                WHERE session_id = ? AND version_number NOT IN (
                    SELECT version_number FROM session_versions
                    WHERE session_id = ?
                    ORDER BY version_number DESC
                    LIMIT ?
                )
                """,
                (session_id, session_id, SESSION_VERSION_RETENTION_LIMIT),
            )

            conn.commit()

            return SessionVersion(
                id=version_id,
                session_id=session_id,
                version_number=next_version,
                data=data,
                summary=summary,
                created_at=created_at,
            )

    def list_for_session(self, session_id: str) -> List[SessionVersion]:
        """List all versions for a session, newest first.

        Deliberately does NOT select `payload` -- that's the whole point of
        the denormalized `summary` column (see migration 092): listing a
        session's history must never require reading (or transferring) every
        stored snapshot. `data` on the returned `SessionVersion` objects is
        always `{}` here; callers must use `get()` for the full payload.
        """
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, session_id, version_number, summary, created_at
                FROM session_versions
                WHERE session_id = ?
                ORDER BY version_number DESC
                """,
                (session_id,),
            )
            rows = cursor.fetchall()
            return [
                SessionVersion(
                    id=row['id'],
                    session_id=row['session_id'],
                    version_number=row['version_number'],
                    data={},
                    summary=row['summary'],
                    created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.now(timezone.utc),
                )
                for row in rows
            ]

    def get(self, session_id: str, version_number: int) -> Optional[SessionVersion]:
        """Get a single version's full record (including payload)."""
        with get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM session_versions WHERE session_id = ? AND version_number = ?",
                (session_id, version_number),
            )
            row = cursor.fetchone()
            return self._row_to_version(row) if row else None
