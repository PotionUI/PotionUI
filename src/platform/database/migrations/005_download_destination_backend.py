"""Migration 005: `downloads` gains `destination_backend_id`.

When set, `DownloadQueue`/`DownloadWorker` fetch the model straight onto that
`native.remote` backend's worker depot instead of the local disk - see
`DownloadQueue.queue_model_download` and `DownloadWorker._fetch_remote`.
`NULL` (the default) is the existing local-destination behaviour, unchanged.

IDEMPOTENT: skips the ALTER when the column already exists.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(downloads)")
        columns = {row[1] for row in cursor.fetchall()}
        if "destination_backend_id" not in columns:
            cursor.execute("ALTER TABLE downloads ADD COLUMN destination_backend_id TEXT")
    print("Migration 005_download_destination_backend: added downloads.destination_backend_id")


def down():
    print("Migration 005_download_destination_backend: no-op (SQLite cannot drop a column pre-3.35)")
