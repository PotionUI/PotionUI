"""
Add file storage directory setting to replace output_directory and temp_directory.
"""

from src.platform.database.database import db


def up():
    """Add file storage directory setting"""
    with db.get_cursor() as cursor:
        # Check if settings table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='settings'
        """)
        if not cursor.fetchone():
            # Settings table doesn't exist yet, skip this migration
            return

        # Add the new file_storage_directory setting
        # Generate a simple ULID-like ID for the migration
        import time
        import random
        import string

        # Simple ULID-like ID generation for migration
        timestamp = int(time.time() * 1000)
        randomness = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        simple_id = f"{timestamp:013d}{randomness}"

        cursor.execute("""
            INSERT OR IGNORE INTO settings (
                id, key, value, value_type, description, type
            ) VALUES (
                ?, 'file_storage_directory', 'storage', 'string',
                'Base directory for all file storage (generations, tmp, models)',
                'SYSTEM'
            )
        """, [simple_id])

        # Update output_directory setting description to indicate deprecation
        cursor.execute("""
            UPDATE settings
            SET description = 'DEPRECATED: Use file_storage_directory instead. Directory where generated images and files are stored'
            WHERE key = 'output_directory'
        """)

        # Update temp_directory setting description to indicate deprecation
        cursor.execute("""
            UPDATE settings
            SET description = 'DEPRECATED: Use file_storage_directory instead. Directory where temporary files are stored'
            WHERE key = 'temp_directory'
        """)


def down():
    """Remove file storage directory setting"""
    with db.get_cursor() as cursor:
        # Remove the file_storage_directory setting
        cursor.execute("DELETE FROM settings WHERE key = 'file_storage_directory'")

        # Restore original descriptions
        cursor.execute("""
            UPDATE settings
            SET description = 'Directory where generated images and files will be stored'
            WHERE key = 'output_directory'
        """)

        cursor.execute("""
            UPDATE settings
            SET description = 'Directory where temporary files will be stored (default: outputs/tmp)'
            WHERE key = 'temp_directory'
        """)