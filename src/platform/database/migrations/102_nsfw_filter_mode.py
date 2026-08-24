"""
Migration 102: replace the boolean `media_nsfw_blur_enabled` per-user setting
with a 3-state `media_nsfw_filter_mode` ('blur' | 'show' | 'hide').

Clean cut, no compat shim: existing `user_settings` overrides of the old key
are remapped onto the new key ('true' -> 'blur', 'false' -> 'show') and the
old setting row plus its overrides are deleted.
"""

import random
import string
import time

from src.platform.database.database import db

_OLD_KEY = "media_nsfw_blur_enabled"
_NEW_KEY = "media_nsfw_filter_mode"
_NEW_DESCRIPTION = (
    "How gallery media rated NSFW by the tagger is shown: blur, show, or "
    "hide. Per-user preference."
)


def _generate_id():
    timestamp = int(time.time() * 1000)
    randomness = "".join(random.choices(string.ascii_uppercase + string.digits, k=10))
    return f"{timestamp:013d}{randomness}"


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if not cursor.fetchone():
            return

        new_setting_id = _generate_id()
        cursor.execute(
            """
            INSERT OR IGNORE INTO settings (
                id, key, value, value_type, description, type
            ) VALUES (?, ?, 'blur', 'string', ?, 'USER')
            """,
            [new_setting_id, _NEW_KEY, _NEW_DESCRIPTION],
        )

        cursor.execute("SELECT id FROM settings WHERE key = ?", [_OLD_KEY])
        old_setting_row = cursor.fetchone()
        if old_setting_row:
            old_setting_id = old_setting_row[0]

            cursor.execute("SELECT id FROM settings WHERE key = ?", [_NEW_KEY])
            new_setting_id = cursor.fetchone()[0]

            cursor.execute(
                "SELECT user_id, value FROM user_settings WHERE setting_id = ?",
                [old_setting_id],
            )
            overrides = cursor.fetchall()
            for user_id, value in overrides:
                mapped_value = "blur" if value == "true" else "show"
                cursor.execute(
                    "SELECT id FROM user_settings WHERE user_id = ? AND setting_id = ?",
                    [user_id, new_setting_id],
                )
                existing = cursor.fetchone()
                if existing:
                    cursor.execute(
                        "UPDATE user_settings SET value = ? WHERE id = ?",
                        [mapped_value, existing[0]],
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO user_settings (id, user_id, setting_id, value)
                        VALUES (?, ?, ?, ?)
                        """,
                        [_generate_id(), user_id, new_setting_id, mapped_value],
                    )

            cursor.execute(
                "DELETE FROM user_settings WHERE setting_id = ?", [old_setting_id]
            )
            cursor.execute("DELETE FROM settings WHERE id = ?", [old_setting_id])


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = ?", [_NEW_KEY])
