"""
Add temp_directory setting to existing settings table
"""

from src.platform.database.database import db

def up():
    """Add temp_directory setting"""
    with db.get_cursor() as cursor:
        # Insert the temp_directory setting
        cursor.execute("""
            INSERT INTO settings (id, key, value, value_type, description, type) VALUES
            ('setting_temp_directory', 'temp_directory', 'outputs/tmp', 'string', 'Directory path for storing temporary files', 'SYSTEM')
        """)

def down():
    """Remove temp_directory setting"""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = 'temp_directory'")