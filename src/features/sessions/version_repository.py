"""
Session Version Repository

Handles database operations for session_versions. See migration 092 for the
schema. Each row is an immutable snapshot of a session's `data` at save time;
the `sessions` table itself stays the "current" state.
"""
from typing import List, Optional
import json
import uuid

from src.platform.database import get_database_connection
from src.platform.database.rows import dt_column, now_utc
from src.features.sessions.dto import SessionVersion

# Maximum historical versions retained per session. The oldest version beyond
# this cap is pruned every time a new one is inserted.
SESSION_VERSION_RETENTION_LIMIT = 50


class SessionVersionRepository:
    """Repository for session_versions database operations."""

    def __init__(self):
        pass

    @staticmethod
    def _decode_payload(payload):
        return json.loads(payload) if isinstance(payload, str) else payload

    def _row_to_version(self, row) -> SessionVersion:
        return SessionVersion(
            id=row['id'],
            session_id=row['session_id'],
            version_number=row['version_number'],
            data=self._decode_payload(row['payload']),
            summary=row['summary'],
            created_at=dt_column(row['created_at']) or now_utc(),
        )

    def create_if_changed(
        self, session_id: str, data: dict, summary: Optional[str]
    ) -> Optional[SessionVersion]:
        with get_database_connection() as conn:
            conn.isolation_level = None
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            try:
                cursor.execute(
                    """
                    SELECT payload FROM session_versions
                    WHERE session_id = ?
                    ORDER BY version_number DESC
                    LIMIT 1
                    """,
                    (session_id,),
                )
                latest_row = cursor.fetchone()
                if latest_row is not None and self._decode_payload(latest_row["payload"]) == data:
                    cursor.execute("ROLLBACK")
                    return None

                result = self._insert_version(cursor, session_id, data, summary)
                cursor.execute("COMMIT")
                return result
            except Exception:
                cursor.execute("ROLLBACK")
                raise

    @staticmethod
    def _insert_version(cursor, session_id: str, data: dict, summary: Optional[str]) -> SessionVersion:
        cursor.execute(
            "SELECT COALESCE(MAX(version_number), 0) FROM session_versions WHERE session_id = ?",
            (session_id,),
        )
        next_version = cursor.fetchone()[0] + 1

        version_id = str(uuid.uuid4())
        created_at = now_utc()

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
                    created_at=dt_column(row['created_at']) or now_utc(),
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
