"""
Migration 115: Make uploads first-class library resources - taggable and
collectable.

`uploads` (087) already records who owns which uploaded file. This migration
gives that row the two things a history generation has and an upload did not:

- `upload_tags` - a junction against the shared `tags` vocabulary, mirroring
  `model_tags` (020) and `generation_tags` (029). Library tags are a new
  `type = 'UPLOAD'` in that same table, so they are user-scoped and never
  collide with a same-named GENERATION tag (the unique index is on
  `(name, type, COALESCE(user_id, ''))`).
- `collection_uploads` - a second junction onto the *existing* `collections`
  table (062), so one folder tree holds both generations and uploads rather
  than a user keeping two parallel sets of folders. `collection_generations`
  is left exactly as it is; nothing is rebuilt here.

Both junctions cascade from their owner rows, so deleting an upload, a tag or
a collection cleans up its memberships without an application-level sweep.
"""

from src.platform.database.database import db


def up():
    """Create upload_tags + collection_uploads junction tables."""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS upload_tags (
                upload_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (upload_id, tag_id),
                FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_upload_tags_upload_id ON upload_tags (upload_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_upload_tags_tag_id ON upload_tags (tag_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS collection_uploads (
                collection_id TEXT NOT NULL,
                upload_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (collection_id, upload_id),
                FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
                FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_uploads_collection_id ON collection_uploads (collection_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_collection_uploads_upload_id ON collection_uploads (upload_id)")


def down():
    """Drop the junction tables."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_collection_uploads_upload_id")
        cursor.execute("DROP INDEX IF EXISTS idx_collection_uploads_collection_id")
        cursor.execute("DROP TABLE IF EXISTS collection_uploads")
        cursor.execute("DROP INDEX IF EXISTS idx_upload_tags_tag_id")
        cursor.execute("DROP INDEX IF EXISTS idx_upload_tags_upload_id")
        cursor.execute("DROP TABLE IF EXISTS upload_tags")
