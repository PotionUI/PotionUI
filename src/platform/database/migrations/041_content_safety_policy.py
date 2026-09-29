from src.platform.database.database import db
from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid

_NAME = "041_content_safety_policy"

_RESTRICTED_GROUP_ID = "restricted_content"
_RESTRICTED_GROUP_NAME = "Restricted content"
_RESTRICTED_GROUP_DESCRIPTION = (
    "Accounts in this group can never generate or see NSFW content. Unrated media stays hidden."
)

_SETTINGS = (
    (
        "content_policy_nsfw",
        "allowed",
        "string",
        "Instance-wide NSFW policy: allowed, blur (rate outputs and flag them) or blocked (rate outputs and never save flagged ones).",
    ),
    (
        "content_banned_words",
        "[]",
        "json",
        "Words and phrases refused in the positive prompt. Case-insensitive, whole word, * matches any word ending.",
    ),
    (
        "content_video_sample_frames",
        "5",
        "integer",
        "Frames sampled from a video (10% to 90% of its duration) when rating it. Capped at 8.",
    ),
)

_CREATE_RATINGS = """
    CREATE TABLE IF NOT EXISTS content_ratings (
        key TEXT PRIMARY KEY,
        nsfw_score REAL,
        state TEXT NOT NULL DEFAULT 'unrated' CHECK (state IN ('safe', 'flagged', 'unrated')),
        rater TEXT,
        source TEXT,
        rated_at TEXT
    )
"""

_CREATE_EVENTS = """
    CREATE TABLE IF NOT EXISTS content_safety_events (
        id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        user_id TEXT,
        generation_id TEXT,
        kind TEXT NOT NULL,
        mode TEXT,
        score REAL,
        detail_json TEXT
    )
"""

_DEFAULT_THRESHOLD = 0.6


def _column_names(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def _table_exists(cursor, table):
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
    return cursor.fetchone() is not None


def _threshold(cursor):
    cursor.execute("SELECT value FROM settings WHERE key = 'media_nsfw_blur_threshold'")
    row = cursor.fetchone()
    try:
        return float(row["value"]) if row else _DEFAULT_THRESHOLD
    except (TypeError, ValueError):
        return _DEFAULT_THRESHOLD


def _seed_settings(cursor, now):
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


def _drop_legacy_setting(cursor):
    cursor.execute("DELETE FROM user_settings WHERE setting_id IN (SELECT id FROM settings WHERE key = 'nsfw')")
    cursor.execute("DELETE FROM settings WHERE key = 'nsfw'")


def _seed_restricted_group(cursor):
    if "content_policy" not in _column_names(cursor, "user_groups"):
        cursor.execute("ALTER TABLE user_groups ADD COLUMN content_policy TEXT")
    cursor.execute(
        """
        INSERT OR IGNORE INTO user_groups (id, name, description, is_system, content_policy)
        VALUES (?, ?, ?, 1, 'blocked')
        """,
        (_RESTRICTED_GROUP_ID, _RESTRICTED_GROUP_NAME, _RESTRICTED_GROUP_DESCRIPTION),
    )
    cursor.execute(
        "UPDATE user_groups SET is_system = 1, content_policy = 'blocked' WHERE id = ?",
        (_RESTRICTED_GROUP_ID,),
    )


def _seed_ratings_from_tags(cursor, now):
    if not _table_exists(cursor, "media_system_tags") or not _table_exists(cursor, "files"):
        return 0
    threshold = _threshold(cursor)
    cursor.execute(
        """
        SELECT f.file_path AS file_path,
               SUM(CASE WHEN t.tag IN ('questionable', 'explicit') THEN t.confidence ELSE 0 END) AS score,
               MAX(t.provenance) AS provenance
        FROM files f
        JOIN media_system_tags t ON t.file_id = f.id AND t.category = 'rating'
        GROUP BY f.id
        """
    )
    seeded = 0
    for row in cursor.fetchall():
        score = float(row["score"] or 0.0)
        state = "flagged" if score >= threshold else "safe"
        cursor.execute(
            """
            INSERT OR IGNORE INTO content_ratings (key, nsfw_score, state, rater, source, rated_at)
            VALUES (?, ?, ?, ?, 'backfill', ?)
            """,
            (row["file_path"], score, state, row["provenance"], now),
        )
        seeded += cursor.rowcount
    return seeded


def up():
    with db.get_cursor() as cursor:
        now = now_iso()
        _seed_settings(cursor, now)
        _drop_legacy_setting(cursor)
        cursor.execute(_CREATE_RATINGS)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_content_ratings_state ON content_ratings (state)")
        cursor.execute(_CREATE_EVENTS)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_content_safety_events_ts ON content_safety_events (ts)")
        _seed_restricted_group(cursor)
        seeded = _seed_ratings_from_tags(cursor, now)
    print(f"Migration {_NAME}: content safety policy installed, {seeded} rating(s) carried into the ledger")


def down():
    with db.get_cursor() as cursor:
        for key, _, _, _ in _SETTINGS:
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
        cursor.execute("DELETE FROM user_groups WHERE id = ?", (_RESTRICTED_GROUP_ID,))
        if "content_policy" in _column_names(cursor, "user_groups"):
            cursor.execute("ALTER TABLE user_groups DROP COLUMN content_policy")
        cursor.execute("DROP TABLE IF EXISTS content_safety_events")
        cursor.execute("DROP TABLE IF EXISTS content_ratings")
        cursor.execute("SELECT 1 FROM settings WHERE key = 'nsfw'")
        if cursor.fetchone() is None:
            now = now_iso()
            cursor.execute(
                """
                INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                VALUES (?, 'nsfw', 'false', 'boolean', 'Allow NSFW content generation', 'USER', ?, ?)
                """,
                (generate_ulid(), now, now),
            )
    print(f"Migration {_NAME}: content safety policy removed")
