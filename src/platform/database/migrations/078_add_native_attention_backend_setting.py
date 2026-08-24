"""
Add the native_attention_backend setting: an admin-pinned attention backend
("auto"/""/sdpa/sage/sage2/flash) for the native engine's Optimizations panel.

Empty string means "auto" - it falls through the precedence chain in
src/core/native/attention.py to $NATIVE_ATTENTION, then the best available
backend.
"""

from src.platform.database.database import db


def up():
    """Add the native_attention_backend setting."""
    with db.get_cursor() as cursor:
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='settings'
        """)
        if not cursor.fetchone():
            # Settings table doesn't exist yet, skip this migration
            return

        import random
        import string
        import time

        timestamp = int(time.time() * 1000)
        randomness = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        simple_id = f"{timestamp:013d}{randomness}"

        cursor.execute("""
            INSERT OR IGNORE INTO settings (
                id, key, value, value_type, description, type
            ) VALUES (
                ?, 'native_attention_backend', '', 'string',
                'Pinned attention backend for the native engine (empty = auto): sdpa, sage, sage2, or flash',
                'SYSTEM'
            )
        """, [simple_id])


def down():
    """Remove the native_attention_backend setting."""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = 'native_attention_backend'")
