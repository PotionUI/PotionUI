"""
Create model_files junction table to link models with their downloaded image files
"""

from src.platform.database.database import db

def up():
    """Create model_files junction table"""
    with db.get_cursor() as cursor:
        # Create model_files junction table
        cursor.execute("""
            CREATE TABLE model_files (
                id TEXT PRIMARY KEY,                   -- ULID primary key
                model_id TEXT NOT NULL,               -- References models(id) with CASCADE delete
                file_id TEXT NOT NULL,                -- References files(id) with CASCADE delete
                file_type TEXT NOT NULL DEFAULT 'image', -- Type: 'image', 'thumbnail', 'preview'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
                UNIQUE(model_id, file_id)             -- Prevent duplicate associations
            )
        """)
        
        # Create indexes for faster lookups
        cursor.execute("CREATE INDEX idx_model_files_model_id ON model_files (model_id)")
        cursor.execute("CREATE INDEX idx_model_files_file_id ON model_files (file_id)")
        cursor.execute("CREATE INDEX idx_model_files_type ON model_files (file_type)")
        cursor.execute("CREATE INDEX idx_model_files_model_type ON model_files (model_id, file_type)")

def down():
    """Drop model_files table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_model_files_model_type")
        cursor.execute("DROP INDEX IF EXISTS idx_model_files_type")
        cursor.execute("DROP INDEX IF EXISTS idx_model_files_file_id")
        cursor.execute("DROP INDEX IF EXISTS idx_model_files_model_id")
        cursor.execute("DROP TABLE IF EXISTS model_files")