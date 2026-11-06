from src.platform.database.database import db
from src.platform.database.rows import now_iso

_NAME = "080_elixirs_media_limits"

_RAISED = (
    ("elixirs_max_media_per_source", "50", "1000"),
    ("elixirs_max_outputs_per_connection", "50", "1000"),
)


def up():
    with db.get_cursor() as cursor:
        now = now_iso()
        for key, old, new in _RAISED:
            cursor.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = ? AND value = ?",
                (new, now, key, old),
            )
    print(f"Migration {_NAME}: elixir media limits raised to 1000 where still at the old default")
