"""
Migration 087: Add uploads table.

Every file a user sends through the MediaLoader form field ("upload a file")
lands in a flat, unowned `storage/uploads/` directory today - there is no
record of who uploaded what, so a user's "Load from uploads" library has
nothing to query. This table gives each upload an owner and the same
best-effort metadata (width/height/duration_seconds/fps/file_size) the
generation `files` table carries for generated media (see 026/086), captured
once at upload time via the shared probe in `src.features.generation.media_probe`
and `ImageProcessor`.

Uploads written before this migration existed are not backfilled - ownership
cannot be inferred from a flat directory of UUID-named files - so they simply
never appear in anyone's library. They remain servable at their existing
`/api/media/uploads/{filename}` URL; only the new library list/delete
endpoints are scoped to this table.
"""

from src.platform.database.database import db


def up():
    """Create the uploads table."""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY,                    -- ULID primary key
                user_id TEXT NOT NULL,                  -- Owning user
                filename TEXT NOT NULL UNIQUE,           -- Unique on-disk name in storage/uploads/
                original_filename TEXT,                  -- Filename as sent by the browser, for display only
                media_type TEXT NOT NULL,                -- 'image' | 'video' | 'audio'
                mime_type TEXT,
                width INTEGER,
                height INTEGER,
                duration_seconds REAL,
                fps REAL,
                file_size INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_uploads_user_created ON uploads (user_id, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_uploads_user_media_type ON uploads (user_id, media_type)")


def down():
    """Drop the uploads table."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS uploads")
