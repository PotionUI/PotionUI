"""
Add the native engine flag settings backing the Optimizations panel toggles:
native_torch_compile and native_stream_prefetch ("on"/"off").

Empty string means "not set by an admin" - the flag falls through to its env
var ($NATIVE_TORCH_COMPILE / $NATIVE_STREAM_PREFETCH, both default off), so
existing env-configured deployments keep working until the toggle is touched.
"""

from src.platform.database.database import db

_SETTINGS = [
    (
        "native_torch_compile",
        "Regional torch.compile for the native engine (empty = follow $NATIVE_TORCH_COMPILE): on or off",
    ),
    (
        "native_stream_prefetch",
        "Streaming layer prefetch under partial residency (empty = follow $NATIVE_STREAM_PREFETCH): on or off",
    ),
]


def up():
    """Add the native engine flag settings."""
    with db.get_cursor() as cursor:
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='settings'
        """)
        if not cursor.fetchone():
            return

        import random
        import string
        import time

        for key, description in _SETTINGS:
            timestamp = int(time.time() * 1000)
            randomness = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
            simple_id = f"{timestamp:013d}{randomness}"

            cursor.execute("""
                INSERT OR IGNORE INTO settings (
                    id, key, value, value_type, description, type
                ) VALUES (
                    ?, ?, '', 'string', ?, 'SYSTEM'
                )
            """, [simple_id, key, description])


def down():
    """Remove the native engine flag settings."""
    with db.get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM settings WHERE key IN ('native_torch_compile', 'native_stream_prefetch')"
        )
