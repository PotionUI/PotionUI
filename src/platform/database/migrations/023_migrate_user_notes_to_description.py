"""
Migration to migrate user_notes to description field
"""

from src.platform.database.database import db

def up():
    """Migrate user_notes to description where description is null"""
    with db.get_cursor() as cursor:
        # Check if columns exist
        cursor.execute("PRAGMA table_info(models)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'user_notes' in columns and 'description' in columns:
            # Migrate user_notes to description where description is null
            cursor.execute("""
                UPDATE models 
                SET description = user_notes 
                WHERE description IS NULL AND user_notes IS NOT NULL
            """)

def down():
    """Rollback not implemented - user_notes data preserved"""
    pass