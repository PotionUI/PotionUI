from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

_KEY = "prompt_segment_pinned_actions"
_DESCRIPTION = "Prompt segment actions shown as pinned icons next to each segment's menu, for every preset and mode."


def up():
    with db.get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM settings WHERE key = ?", (_KEY,))
        if cursor.fetchone() is not None:
            print(f"Migration 032_prompt_segment_pinned_actions: {_KEY} already present")
            return
        now = now_iso()
        cursor.execute(
            """
            INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'USER', ?, ?)
            """,
            (generate_ulid(), _KEY, "[]", "json", _DESCRIPTION, now, now),
        )
    print(f"Migration 032_prompt_segment_pinned_actions: seeded {_KEY}")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = ?", (_KEY,))
    print(f"Migration 032_prompt_segment_pinned_actions: removed {_KEY}")
