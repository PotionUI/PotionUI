"""
Migration to add files system for storing model-related files locally
"""

from src.platform.database.database import db

def up():
    """Extend existing files and model_files tables for model management"""
    with db.get_cursor() as cursor:
        # Extend files table with additional columns needed for model files
        cursor.execute("PRAGMA table_info(files)")
        files_columns = [col[1] for col in cursor.fetchall()]
        
        # Add hash column if it doesn't exist
        if 'hash' not in files_columns:
            cursor.execute('ALTER TABLE files ADD COLUMN hash TEXT')
        
        # Add filename column if it doesn't exist  
        if 'filename' not in files_columns:
            cursor.execute('ALTER TABLE files ADD COLUMN filename TEXT')
        
        # Add mime_type column if it doesn't exist
        if 'mime_type' not in files_columns:
            cursor.execute('ALTER TABLE files ADD COLUMN mime_type TEXT')
        
        # Extend model_files table with display_order
        cursor.execute("PRAGMA table_info(model_files)")
        model_files_columns = [col[1] for col in cursor.fetchall()]
        
        if 'display_order' not in model_files_columns:
            cursor.execute('ALTER TABLE model_files ADD COLUMN display_order INTEGER DEFAULT 0')
        
        # Create index for file hash lookups
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_files_hash ON files(hash)')

def down():
    """Drop model files system extensions"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_files_hash")
        # Note: We don't drop the files table, model_files table, or remove columns as they might be used elsewhere