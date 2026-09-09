"""Migration 020: thumbnail rendering becomes an admin setting.

Seeds the five SYSTEM settings that describe a thumbnail profile - which
sizes are rendered, and the fps/duration/quality of an animated video
preview - defaulting to the balanced profile (one medium size, 12 fps, 3 s).
The generators previously hard-coded three sizes at the source frame rate,
which is the `full` profile.

Also adds `thumbnail_profile` to `files` and `uploads`: the fingerprint of the
profile a row's thumbnails were rendered under. NULL means "rendered before
this migration", which the regeneration job treats as stale.

IDEMPOTENT: each column is added only when missing, each setting only when
absent.
"""

from datetime import datetime

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid

_COLUMNS = (
    ("files", "thumbnail_profile", "TEXT"),
    ("uploads", "thumbnail_profile", "TEXT"),
)

_SETTINGS = (
    (
        "thumbnail_sizes",
        '["medium"]',
        "json",
        "Which thumbnail sizes are rendered for new images and videos: any of small (480px), medium (768px), large (1024px).",
    ),
    (
        "thumbnail_video_fps",
        "12",
        "integer",
        "Frame rate of an animated video thumbnail. Lower is smaller on disk; animated WebP has no inter-frame compression.",
    ),
    (
        "thumbnail_video_seconds",
        "3",
        "integer",
        "How many seconds of a video the animated thumbnail covers.",
    ),
    (
        "thumbnail_video_quality",
        "50",
        "integer",
        "WebP quality (1-100) of an animated video thumbnail.",
    ),
    (
        "thumbnail_image_quality",
        "85",
        "integer",
        "WebP quality (1-100) of an image thumbnail.",
    ),
)


def up():
    added = []
    seeded = []
    with db.get_cursor() as cursor:
        for table, column, ddl in _COLUMNS:
            cursor.execute(f"PRAGMA table_info({table})")
            existing = {row[1] for row in cursor.fetchall()}
            if column in existing:
                continue
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added.append(f"{table}.{column}")

        now = datetime.now().isoformat()
        for key, value, value_type, description in _SETTINGS:
            cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if cursor.fetchone() is not None:
                continue
            cursor.execute(
                """
                INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'SYSTEM', ?, ?)
                """,
                (generate_ulid(), key, value, value_type, description, now, now),
            )
            seeded.append(key)

    print(
        f"Migration 020_thumbnail_profiles: added {added or 'no'} column(s), "
        f"seeded {len(seeded)} setting(s)"
    )


def down():
    print("Migration 020_thumbnail_profiles: no-op (SQLite keeps the added columns)")
