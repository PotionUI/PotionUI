"""
Notification Repository

Handles database operations for per-user notifications. Returns DTOs, not
database models.
"""
import json
import logging
from typing import List, Optional
from datetime import datetime

from src.platform.database import db
from src.features.notifications.records import Notification, NotificationLevel
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)


class NotificationRepository:
    """Repository for managing persisted notifications."""

    def _row_to_notification(self, row) -> Optional[Notification]:
        """Convert a database row to a Notification DTO."""
        if not row:
            return None

        metadata = None
        if row['metadata']:
            try:
                metadata = json.loads(row['metadata'])
            except (ValueError, TypeError):
                metadata = None

        created_at = row['created_at']
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                created_at = datetime.now()

        try:
            row_type = row['type'] or ''
        except (KeyError, IndexError):
            row_type = ''

        return Notification(
            id=row['id'],
            user_id=row['user_id'],
            category=row['category'],
            level=NotificationLevel(row['level']),
            title=row['title'],
            message=row['message'] or '',
            metadata=metadata,
            source=row['source'],
            type=row_type,
            read=bool(row['read']),
            created_at=created_at or datetime.now()
        )

    def create(
        self,
        *,
        user_id: str,
        category: str,
        level: str,
        title: str,
        message: str = "",
        metadata: Optional[dict] = None,
        source: str = "core",
        type: str = ""
    ) -> Notification:
        """Create a new notification row and return it."""
        notification_id = generate_ulid()
        metadata_json = json.dumps(metadata) if metadata is not None else None
        now = datetime.now()

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO notifications
                    (id, user_id, category, level, title, message, metadata, source, type, read, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """, (
                notification_id, user_id, category, level, title, message,
                metadata_json, source, type, now.isoformat()
            ))

        return Notification(
            id=notification_id,
            user_id=user_id,
            category=category,
            level=NotificationLevel(level),
            title=title,
            message=message,
            metadata=metadata,
            source=source,
            type=type,
            read=False,
            created_at=now
        )

    def list_for_user(
        self,
        user_id: str,
        limit: int = 50,
        before_id: Optional[str] = None,
        unread_only: bool = False
    ) -> List[Notification]:
        """List notifications for a user, newest first, with keyset pagination."""
        query = "SELECT * FROM notifications WHERE user_id = ?"
        params = [user_id]

        if unread_only:
            query += " AND read = 0"

        if before_id:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [self._row_to_notification(row) for row in cursor.fetchall()]

    def unread_count(self, user_id: str) -> int:
        """Count unread notifications for a user."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND read = 0",
                (user_id,)
            )
            row = cursor.fetchone()
            return row['cnt'] if row else 0

    def mark_read(self, notification_id: str, user_id: str) -> bool:
        """Mark a single notification as read for its owning user."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE notifications SET read = 1 WHERE id = ? AND user_id = ?",
                (notification_id, user_id)
            )
            return cursor.rowcount > 0

    def mark_all_read(self, user_id: str) -> int:
        """Mark all of a user's notifications as read. Returns rows updated."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE notifications SET read = 1 WHERE user_id = ? AND read = 0",
                (user_id,)
            )
            return cursor.rowcount

    def delete(self, notification_id: str, user_id: str) -> bool:
        """Delete a single notification owned by the user."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM notifications WHERE id = ? AND user_id = ?",
                (notification_id, user_id)
            )
            return cursor.rowcount > 0

    def delete_all(self, user_id: str) -> int:
        """Delete all notifications for a user. Returns rows deleted."""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
            return cursor.rowcount

    def prune(self, user_id: str, keep: int = 200) -> int:
        """Delete the oldest notifications beyond `keep` most-recent rows for a user."""
        with db.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM notifications
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM notifications
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
            """, (user_id, user_id, keep))
            return cursor.rowcount


# Global repository instance - for backward compatibility only
# Prefer using DI-injected NotificationRepository instead
notification_repo = NotificationRepository()
