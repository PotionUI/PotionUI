"""
Add output_directory setting to existing settings table
"""

from src.platform.database.database import db

def up():
    """Add output_directory setting"""
    with db.get_cursor() as cursor:
        # Insert the output_directory setting (ignore if already exists from earlier migration)
        cursor.execute("""
            INSERT OR IGNORE INTO settings (id, key, value, value_type, description, type) VALUES
            ('setting_output_directory', 'output_directory', 'outputs', 'string', 'Directory path for storing generated outputs', 'SYSTEM')
        """)

def down():
    """Remove output_directory setting"""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM settings WHERE key = 'output_directory'")