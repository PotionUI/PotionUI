"""
Migration 110: content digests on model_availability, and a scan-time hash cache.

Remote execution mirrors the model depot: a worker mounts it at the same path the
dispatcher uses, so a locally-computed model path resolves verbatim on the worker too.
That answers *where* a file is. It says nothing about whether it is the *same* file - a
partially-synced volume, an interrupted upload, or a worker one rsync behind produces a
file at exactly the right path with different bytes. Today that generation would succeed
silently on the wrong weights: no error, no crash, just an image nobody asked for.

`models.sha256` already carries a real content digest - but only for a regular file, and
only once something has actually read its bytes (`ModelScanner.calculate_sha256`, run by
the full-library indexer). Two gaps this migration addresses:

1. `model_availability` - the per-backend claim that a backend can load a model - carries
   no digest of its own. Two backends (this host's native engine and a remote native
   worker's mirror, say) can each claim to hold the same model_id with no way to tell
   whether their bytes agree. `digest` records what THAT backend's scan actually computed
   for its own copy, so a disagreement against the model's canonical `models.sha256` is
   detectable per backend rather than assumed away.

2. `src/features/backends/native_model_scan.py` does no hashing at all today - it only
   reuses `models.sha256` when a path happens to already have a row. A file dropped into
   `models/checkpoints` by hand gets a row and no digest, which is exactly the file most
   likely to differ between hosts. `model_hash_cache` lets the native scan hash on demand
   without re-hashing every multi-GB checkpoint on every scan: keyed by (path, size,
   mtime_ns), a cache hit means the file hasn't changed since it was last hashed, so the
   scan can skip straight to the cached digest.

Both are additive: `model_availability.digest` is a nullable column (SQLite has no cheap
DROP COLUMN, so `down()` leaves it in place - same tradeoff as 104_add_file_is_derived),
and `model_hash_cache` is pure working storage, safe to drop and rebuild from nothing.

Directory-model fingerprints (`models.sha256` for `is_directory` rows - see
101_add_model_is_directory.py) are NOT content hashes and are never compared against
`digest`; see src/features/models/backend_indexer.py.
"""

from src.platform.database.database import db


def up():
    """Add model_availability.digest and create model_hash_cache."""
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(model_availability)")
        availability_columns = [col[1] for col in cursor.fetchall()]

        if 'digest' not in availability_columns:
            cursor.execute("ALTER TABLE model_availability ADD COLUMN digest TEXT")

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_availability_digest
            ON model_availability (digest)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_hash_cache (
                path TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                hashed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        print("Migration 110: model_availability.digest added; model_hash_cache created")


def down():
    """Drop model_hash_cache. model_availability.digest is left in place."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS model_hash_cache")
        cursor.execute("DROP INDEX IF EXISTS idx_availability_digest")
        print("Migration 110: dropped model_hash_cache (model_availability.digest left in place)")
