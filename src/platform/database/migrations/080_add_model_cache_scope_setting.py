"""
Add the model_cache_scope setting: how the native model RAM cache is scoped
across preset switches. Without a seeded row, PUT /api/settings/model_cache_scope
returned setting_not_found and an admin could not flip it through the app.

Values:
  - 'preset' (default): evict a previous preset's cached models when you switch
    presets, so host RAM holds only the active preset's models.
  - 'global': keep every preset's models cached until RAM pressure forces LRU
    eviction (the pre-preset-scoping behaviour).

Read by ModelLifecycleManager (SettingsManager.get_model_cache_scope), which
clamps any unrecognised value back to 'preset'.
"""

from src.platform.database.database import db


def up():
    """Add the model_cache_scope setting (default 'preset')."""
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

        description = (
            "How the native model RAM cache is scoped across preset switches: "
            "preset (evict the previous preset's models on switch) or "
            "global (keep all cached until RAM pressure)"
        )
        cursor.execute("""
            INSERT OR IGNORE INTO settings (
                id, key, value, value_type, description, type
            ) VALUES (?, 'model_cache_scope', 'preset', 'string', ?, 'SYSTEM')
        """, [simple_id, description])


def down():
    """Remove the model_cache_scope setting."""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = 'model_cache_scope'")
