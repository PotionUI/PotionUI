"""Downloads move into core with grouped Hugging Face repo jobs.

Rebuilds the `downloads` table (SQLite cannot alter CHECK constraints) to:
- add `group_id` (parent download id for grouped children), `repo_id` and
  `revision` (hf_repo parents),
- drop the closed `type` CHECK so new job kinds ('hf_repo', future ones)
  don't need a table rebuild each time; `status` keeps its CHECK.

Existing download history is preserved. The dissolved `downloader` plugin's
registration rows are removed - its queue/worker/API are core now.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("ALTER TABLE downloads RENAME TO downloads_legacy")

        cursor.execute("""
            CREATE TABLE downloads (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL DEFAULT 'model',
                url TEXT NOT NULL,
                destination_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress REAL DEFAULT 0.0,
                total_bytes INTEGER,
                downloaded_bytes INTEGER DEFAULT 0,
                speed_bytes_per_sec REAL,
                error_message TEXT,
                provider_id TEXT,
                tags TEXT,
                checksum_sha256 TEXT,
                retry_count INTEGER DEFAULT 0,
                group_id TEXT,
                repo_id TEXT,
                revision TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                created_by TEXT,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY (group_id) REFERENCES downloads(id) ON DELETE CASCADE,
                CHECK (status IN ('pending', 'downloading', 'paused', 'completed', 'failed', 'cancelled'))
            )
        """)

        cursor.execute("""
            INSERT INTO downloads (
                id, type, url, destination_path, filename, status, progress,
                total_bytes, downloaded_bytes, speed_bytes_per_sec, error_message,
                provider_id, tags, checksum_sha256, retry_count,
                created_at, started_at, completed_at, created_by
            )
            SELECT
                id, type, url, destination_path, filename, status, progress,
                total_bytes, downloaded_bytes, speed_bytes_per_sec, error_message,
                provider_id, tags, checksum_sha256, retry_count,
                created_at, started_at, completed_at, created_by
            FROM downloads_legacy
        """)

        cursor.execute("DROP TABLE downloads_legacy")

        cursor.execute("CREATE INDEX idx_downloads_status ON downloads (status)")
        cursor.execute("CREATE INDEX idx_downloads_type ON downloads (type)")
        cursor.execute("CREATE INDEX idx_downloads_created_by ON downloads (created_by)")
        cursor.execute("CREATE INDEX idx_downloads_created_at ON downloads (created_at)")
        cursor.execute("CREATE INDEX idx_downloads_group_id ON downloads (group_id)")

        cursor.execute("DELETE FROM plugin_settings WHERE plugin_id = 'downloader'")
        cursor.execute("DELETE FROM plugin_pages WHERE plugin_id = 'downloader'")
        cursor.execute("DELETE FROM plugins WHERE id = 'downloader'")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_downloads_group_id")
        cursor.execute("DELETE FROM downloads WHERE group_id IS NOT NULL")
        cursor.execute("DELETE FROM downloads WHERE type NOT IN ('model', 'media')")
