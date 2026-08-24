"""
Create downloads table for managing download tasks.
"""

from src.platform.database.database import db


def up():
    """Create downloads table"""
    with db.get_cursor() as cursor:
        # Create downloads table
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                created_by TEXT,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
                CHECK (type IN ('model', 'media')),
                CHECK (status IN ('pending', 'downloading', 'paused', 'completed', 'failed', 'cancelled'))
            )
        """)

        # Create indexes for common queries
        cursor.execute("CREATE INDEX idx_downloads_status ON downloads (status)")
        cursor.execute("CREATE INDEX idx_downloads_type ON downloads (type)")
        cursor.execute("CREATE INDEX idx_downloads_created_by ON downloads (created_by)")
        cursor.execute("CREATE INDEX idx_downloads_created_at ON downloads (created_at)")

        # Create trigger to update updated_at (though we don't have an updated_at column,
        # keeping pattern consistent with other tables for future-proofing)


def down():
    """Drop downloads table"""
    with db.get_cursor() as cursor:
        # Drop indexes
        cursor.execute("DROP INDEX IF EXISTS idx_downloads_created_at")
        cursor.execute("DROP INDEX IF EXISTS idx_downloads_created_by")
        cursor.execute("DROP INDEX IF EXISTS idx_downloads_type")
        cursor.execute("DROP INDEX IF EXISTS idx_downloads_status")

        # Drop table
        cursor.execute("DROP TABLE IF EXISTS downloads")
