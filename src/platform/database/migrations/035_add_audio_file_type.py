"""
Migration to add AUDIO as a valid file_type for audio generation outputs.

This migration handles the case where the files table has a CHECK constraint
on file_type that only allows 'IMAGE' and 'VIDEO'. We need to recreate
the table to update the constraint to also allow 'AUDIO'.

Note: SQLite doesn't support ALTER TABLE to modify constraints, so we need
to recreate the table if there's a constraint.
"""

from src.platform.database.database import db
import sqlite3


def up():
    """Add AUDIO as a valid file_type"""
    with db.get_connection() as conn:
        # First check if we're in a broken state (files_old exists but files doesn't)
        cursor = conn.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='files_old'
        """)
        has_files_old = cursor.fetchone() is not None

        cursor = conn.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='files'
        """)
        has_files = cursor.fetchone() is not None

        # Recovery: if files_old exists but files doesn't, we need to recover
        if has_files_old and not has_files:
            # Get columns from files_old
            cursor = conn.execute("PRAGMA table_info(files_old)")
            columns_info = cursor.fetchall()
            column_names = [col[1] for col in columns_info]
            columns_list = ", ".join(column_names)

            # Disable foreign keys temporarily
            conn.execute("PRAGMA foreign_keys = OFF")

            # Create new table with AUDIO support
            conn.execute("""
                CREATE TABLE files (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    file_type TEXT NOT NULL CHECK(file_type IN ('IMAGE', 'VIDEO', 'AUDIO')),
                    file_size INTEGER,
                    pipe_name TEXT,
                    is_final BOOLEAN DEFAULT FALSE,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    hash TEXT,
                    filename TEXT,
                    mime_type TEXT,
                    thumbnail_small TEXT,
                    thumbnail_medium TEXT,
                    thumbnail_large TEXT,
                    width INTEGER,
                    height INTEGER,
                    updated_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Copy data from old table
            conn.execute(f"""
                INSERT INTO files ({columns_list})
                SELECT {columns_list} FROM files_old
            """)

            # Drop old table
            conn.execute("DROP TABLE files_old")

            # Recreate indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_file_type ON files (file_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_user_id ON files (user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_created_at ON files (created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files (hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_mime_type ON files (mime_type)")

            # Re-enable foreign keys
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()
            return

        # Normal case: files table exists
        if not has_files:
            # Files table doesn't exist yet, skip this migration
            return

        # Get current table schema to check for CHECK constraint
        cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='files'")
        result = cursor.fetchone()
        if not result:
            return

        table_sql = result[0] if result else ""

        # Check if there's a CHECK constraint on file_type that doesn't include AUDIO
        if "CHECK" in table_sql and "file_type" in table_sql and "AUDIO" not in table_sql:
            # Need to recreate table with updated constraint
            # First, get current columns
            cursor = conn.execute("PRAGMA table_info(files)")
            columns_info = cursor.fetchall()
            column_names = [col[1] for col in columns_info]

            # Create column list for SELECT
            columns_list = ", ".join(column_names)

            # Disable foreign keys temporarily
            conn.execute("PRAGMA foreign_keys = OFF")

            # Rename old table
            conn.execute("ALTER TABLE files RENAME TO files_old")

            # Create new table with updated constraint
            conn.execute("""
                CREATE TABLE files (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    file_type TEXT NOT NULL CHECK(file_type IN ('IMAGE', 'VIDEO', 'AUDIO')),
                    file_size INTEGER,
                    pipe_name TEXT,
                    is_final BOOLEAN DEFAULT FALSE,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    hash TEXT,
                    filename TEXT,
                    mime_type TEXT,
                    thumbnail_small TEXT,
                    thumbnail_medium TEXT,
                    thumbnail_large TEXT,
                    width INTEGER,
                    height INTEGER,
                    updated_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            # Copy data from old table
            conn.execute(f"""
                INSERT INTO files ({columns_list})
                SELECT {columns_list} FROM files_old
            """)

            # Drop old table
            conn.execute("DROP TABLE files_old")

            # Recreate indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_file_type ON files (file_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_user_id ON files (user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_created_at ON files (created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files (hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_mime_type ON files (mime_type)")

            # Re-enable foreign keys
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()


def down():
    """Remove AUDIO from valid file_type values"""
    with db.get_cursor() as cursor:
        # Check if files table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='files'
        """)
        if not cursor.fetchone():
            return

        # Delete any AUDIO file records first
        cursor.execute("DELETE FROM files WHERE file_type = 'AUDIO'")

        # Get current table schema
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='files'")
        result = cursor.fetchone()
        if not result:
            return

        table_sql = result[0] if result else ""

        # Only recreate if we have AUDIO in CHECK constraint
        if "AUDIO" in table_sql:
            cursor.execute("PRAGMA table_info(files)")
            columns_info = cursor.fetchall()
            column_names = [col[1] for col in columns_info]
            columns_list = ", ".join(column_names)

            cursor.execute("ALTER TABLE files RENAME TO files_old")

            # Recreate without AUDIO in constraint
            cursor.execute("""
                CREATE TABLE files (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    file_type TEXT NOT NULL CHECK(file_type IN ('IMAGE', 'VIDEO')),
                    file_size INTEGER,
                    pipe_name TEXT,
                    is_final BOOLEAN DEFAULT FALSE,
                    user_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    hash TEXT,
                    filename TEXT,
                    mime_type TEXT,
                    thumbnail_small TEXT,
                    thumbnail_medium TEXT,
                    thumbnail_large TEXT,
                    width INTEGER,
                    height INTEGER,
                    updated_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)

            cursor.execute(f"""
                INSERT INTO files ({columns_list})
                SELECT {columns_list} FROM files_old
            """)

            cursor.execute("DROP TABLE files_old")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_file_type ON files (file_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_user_id ON files (user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_created_at ON files (created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files (hash)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_mime_type ON files (mime_type)")
