"""
Create generations table
"""

from src.platform.database.database import db

def up():
    """Create generations table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE generations (
                id TEXT PRIMARY KEY,
                preset_name TEXT NOT NULL,
                preset_version TEXT,
                form_data TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress REAL DEFAULT 0.0,
                current_step TEXT,
                current_step_num INTEGER DEFAULT 0,
                total_steps INTEGER DEFAULT 0,
                error_message TEXT,
                output_directory TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create trigger to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_generations_updated_at 
            AFTER UPDATE ON generations 
            FOR EACH ROW 
            BEGIN 
                UPDATE generations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        # Create generation_files table for tracking output files
        cursor.execute("""
            CREATE TABLE generation_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generation_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER,
                pipe_name TEXT,
                is_final BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations (id) ON DELETE CASCADE
            )
        """)
        
        # Create index for faster queries
        cursor.execute("CREATE INDEX idx_generations_status ON generations (status)")
        cursor.execute("CREATE INDEX idx_generations_created_at ON generations (created_at)")
        cursor.execute("CREATE INDEX idx_generation_files_generation_id ON generation_files (generation_id)")

def down():
    """Drop generations tables"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TRIGGER IF EXISTS update_generations_updated_at")
        cursor.execute("DROP INDEX IF EXISTS idx_generations_status")
        cursor.execute("DROP INDEX IF EXISTS idx_generations_created_at")
        cursor.execute("DROP INDEX IF EXISTS idx_generation_files_generation_id")
        cursor.execute("DROP TABLE IF EXISTS generation_files")
        cursor.execute("DROP TABLE IF EXISTS generations")