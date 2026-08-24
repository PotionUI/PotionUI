"""
Migration to add a `type` column to notifications (for per-user notification
type preferences) and seed the `notification_preferences` USER setting.
"""

from src.platform.database.database import db


def up():
    """Add notifications.type column and seed the notification_preferences setting."""
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(notifications)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'type' not in columns:
            cursor.execute('''
                ALTER TABLE notifications
                ADD COLUMN type TEXT NOT NULL DEFAULT ''
            ''')

        cursor.execute("SELECT id FROM settings WHERE key = 'notification_preferences'")
        if cursor.fetchone() is None:
            cursor.execute("""
                INSERT INTO settings (id, key, value, value_type, description, type) VALUES
                ('setting_notification_preferences', 'notification_preferences', '{}', 'json',
                 'Per-user notification preferences (enabled types + sound)', 'USER')
            """)


def down():
    """Rollback the migration - SQLite doesn't support dropping columns easily."""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = 'notification_preferences'")
    # SQLite doesn't support DROP COLUMN directly; leave `type` column in place.
