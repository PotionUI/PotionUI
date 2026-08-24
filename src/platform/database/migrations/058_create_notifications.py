"""
Migration to create the notifications table.

Persists per-user notifications (with system-wide broadcasts fanned out to
one row per user at creation time). See docs/presets.md sibling
docs/notifications.md (if present) or src/core/notification/ for details.
"""

from src.platform.database.database import db


def up():
    """Create notifications table and its indexes."""
    with db.get_cursor() as cursor:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'system',
                level TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                metadata TEXT,
                source TEXT NOT NULL DEFAULT 'core',
                read INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_notifications_user_created
            ON notifications(user_id, id DESC)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_notifications_user_read
            ON notifications(user_id, read)
        ''')


def down():
    """Rollback the migration - drop the notifications table."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS notifications")
