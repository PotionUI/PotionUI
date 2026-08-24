"""
Refactor file storage structure to unify directory management.
- Add mime_type column if not exists
- Update file_type values to uppercase (IMAGE, VIDEO)
- Note: hash and filename columns kept if they exist (can be ignored)
"""

from src.platform.database.database import db


def up():
    """Update files table structure for unified storage"""
    with db.get_cursor() as cursor:
        # Check if files table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='files'
        """)
        if not cursor.fetchone():
            # Files table doesn't exist yet, skip this migration
            return

        # Check what columns exist in files table
        cursor.execute("PRAGMA table_info(files)")
        files_columns = [col[1] for col in cursor.fetchall()]

        # Add mime_type column if it doesn't exist
        if 'mime_type' not in files_columns:
            cursor.execute("ALTER TABLE files ADD COLUMN mime_type TEXT")

        # Update file_type values to uppercase
        cursor.execute("""
            UPDATE files
            SET file_type = CASE
                WHEN file_type = 'image' THEN 'IMAGE'
                WHEN file_type = 'video' THEN 'VIDEO'
                WHEN file_type LIKE '%image%' THEN 'IMAGE'
                WHEN file_type LIKE '%video%' THEN 'VIDEO'
                ELSE UPPER(file_type)
            END
        """)

        # Add mime_type values based on file extensions if not set
        cursor.execute("""
            UPDATE files
            SET mime_type = CASE
                WHEN file_path LIKE '%.png' THEN 'image/png'
                WHEN file_path LIKE '%.jpg' OR file_path LIKE '%.jpeg' THEN 'image/jpeg'
                WHEN file_path LIKE '%.gif' THEN 'image/gif'
                WHEN file_path LIKE '%.webp' THEN 'image/webp'
                WHEN file_path LIKE '%.mp4' THEN 'video/mp4'
                WHEN file_path LIKE '%.avi' THEN 'video/avi'
                WHEN file_path LIKE '%.mov' THEN 'video/quicktime'
                ELSE 'application/octet-stream'
            END
            WHERE mime_type IS NULL
        """)

        # Create index for mime_type if it doesn't exist
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_mime_type ON files (mime_type)")


def down():
    """Revert file storage structure changes"""
    with db.get_cursor() as cursor:
        # Revert file_type values to lowercase
        cursor.execute("""
            UPDATE files
            SET file_type = CASE
                WHEN file_type = 'IMAGE' THEN 'image'
                WHEN file_type = 'VIDEO' THEN 'video'
                ELSE LOWER(file_type)
            END
        """)

        # Note: We don't remove the mime_type column as it might be useful
        # and SQLite doesn't support DROP COLUMN easily