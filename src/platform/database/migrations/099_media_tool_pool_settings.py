from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

_NAME = "099_media_tool_pool_settings"

_SETTINGS = (
    ("media_tools_max_concurrent", 0, "Media tool runs that may encode at the same time; 0 picks one per 4 CPU cores, between 2 and 8"),
    ("media_tools_max_previews", 2, "Live previews that may render at the same time"),
    ("media_tools_max_running_per_user", 0, "Media tool runs one user may have encoding at the same time; 0 means no per-user limit"),
    ("media_tools_max_queued_per_user", 8, "Media tool runs one user may have waiting before new ones are refused"),
    ("media_tools_ffmpeg_threads", 0, "CPU threads each ffmpeg process may use; 0 splits the CPU cores evenly across the concurrent runs"),
)


def up():
    seeded = 0
    with db.get_cursor() as cursor:
        for key, value, description in _SETTINGS:
            cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if cursor.fetchone() is not None:
                continue
            now = now_iso()
            cursor.execute(
                """
                INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                VALUES (?, ?, ?, 'integer', ?, 'SYSTEM', ?, ?)
                """,
                (generate_ulid(), key, str(value), description, now, now),
            )
            seeded += 1
    print(f"Migration {_NAME}: {seeded} setting(s) seeded")
