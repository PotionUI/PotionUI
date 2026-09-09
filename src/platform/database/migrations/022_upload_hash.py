"""Migration 022: content hash on uploads, for dedup by hash.

Adds `content_hash` (sha256 hex digest of the uploaded bytes) to `uploads`,
plus a `(user_id, content_hash)` index so `UploadRepository.find_by_hash` can
look up a candidate for reuse without a table scan. Existing rows are not
backfilled - hashing every file already on disk at migration time is too slow
to run inline, so pre-existing uploads keep a NULL hash and simply never
dedupe until they are re-uploaded (a fresh row is written, matching the hash
of any future duplicate).

IDEMPOTENT: the column is added only when missing, the index only when absent.
"""

from src.platform.database.database import db

_COLUMN = ("uploads", "content_hash", "TEXT")
_INDEX = "idx_uploads_user_hash"


def up():
    added = None
    with db.get_cursor() as cursor:
        table, column, ddl = _COLUMN
        cursor.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cursor.fetchall()}
        if column not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added = f"{table}.{column}"

        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS {_INDEX} ON uploads(user_id, content_hash)"
        )

    print(
        f"Migration 022_upload_hash: added {added or 'no'} column, "
        f"ensured index {_INDEX}"
    )


def down():
    print("Migration 022_upload_hash: no-op (SQLite keeps the added column/index)")
