"""
Create models table for model indexing and management
"""

from src.platform.database.database import db

def up():
    """Create models table"""
    with db.get_cursor() as cursor:
        # Create models table
        cursor.execute("""
            CREATE TABLE models (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL UNIQUE,
                file_size INTEGER,
                sha256 TEXT UNIQUE,
                model_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create trigger to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_models_updated_at 
            AFTER UPDATE ON models 
            FOR EACH ROW 
            BEGIN 
                UPDATE models SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        # Create model_civitai_info table for Civitai metadata
        cursor.execute("""
            CREATE TABLE model_civitai_info (
                model_id TEXT PRIMARY KEY,
                civitai_model_id INTEGER,
                version_id INTEGER,
                name TEXT,
                description TEXT,
                tags TEXT,  -- JSON array
                nsfw BOOLEAN DEFAULT FALSE,
                images TEXT,  -- JSON array of image URLs
                download_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models (id) ON DELETE CASCADE
            )
        """)
        
        # Create trigger to update civitai info updated_at
        cursor.execute("""
            CREATE TRIGGER update_model_civitai_info_updated_at 
            AFTER UPDATE ON model_civitai_info 
            FOR EACH ROW 
            BEGIN 
                UPDATE model_civitai_info SET updated_at = CURRENT_TIMESTAMP WHERE model_id = NEW.model_id;
            END
        """)
        
        # Create indexes for faster queries
        cursor.execute("CREATE INDEX idx_models_model_type ON models (model_type)")
        cursor.execute("CREATE INDEX idx_models_sha256 ON models (sha256)")
        cursor.execute("CREATE INDEX idx_models_filename ON models (filename)")
        cursor.execute("CREATE INDEX idx_models_indexed_at ON models (indexed_at)")
        cursor.execute("CREATE INDEX idx_model_civitai_info_civitai_model_id ON model_civitai_info (civitai_model_id)")

def down():
    """Drop models tables"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TRIGGER IF EXISTS update_models_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_model_civitai_info_updated_at")
        cursor.execute("DROP INDEX IF EXISTS idx_models_model_type")
        cursor.execute("DROP INDEX IF EXISTS idx_models_sha256")
        cursor.execute("DROP INDEX IF EXISTS idx_models_filename")
        cursor.execute("DROP INDEX IF EXISTS idx_models_indexed_at")
        cursor.execute("DROP INDEX IF EXISTS idx_model_civitai_info_civitai_model_id")
        cursor.execute("DROP TABLE IF EXISTS model_civitai_info")
        cursor.execute("DROP TABLE IF EXISTS models")