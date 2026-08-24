"""
Rename preset_name column to preset_id for better semantic clarity
"""

from src.platform.database.database import db

def up():
    """Rename preset_name to preset_id"""
    with db.get_cursor() as cursor:
        # SQLite doesn't support RENAME COLUMN directly, so we need to:
        # 1. Create new table with correct schema
        # 2. Copy data
        # 3. Drop old table
        # 4. Rename new table
        
        # Create new table with correct schema
        cursor.execute("""
            CREATE TABLE generations_new (
                id TEXT PRIMARY KEY,
                preset_id TEXT NOT NULL,
                preset_version TEXT,
                form_data TEXT NOT NULL,
                user_id TEXT,
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
        
        # Copy data from old table to new table
        cursor.execute("""
            INSERT INTO generations_new (
                id, preset_id, preset_version, form_data, user_id, status, progress,
                current_step, current_step_num, total_steps, error_message, output_directory,
                created_at, started_at, completed_at, updated_at
            )
            SELECT 
                id, preset_name, preset_version, form_data, user_id, status, progress,
                current_step, current_step_num, total_steps, error_message, output_directory,
                created_at, started_at, completed_at, updated_at
            FROM generations
        """)
        
        # Drop old table
        cursor.execute("DROP TABLE generations")
        
        # Rename new table
        cursor.execute("ALTER TABLE generations_new RENAME TO generations")
        
        # Recreate trigger
        cursor.execute("""
            CREATE TRIGGER update_generations_updated_at 
            AFTER UPDATE ON generations 
            FOR EACH ROW 
            BEGIN 
                UPDATE generations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        # Recreate indexes
        cursor.execute("CREATE INDEX idx_generations_status ON generations (status)")
        cursor.execute("CREATE INDEX idx_generations_created_at ON generations (created_at)")
        cursor.execute("CREATE INDEX idx_generations_user_id ON generations (user_id)")

def down():
    """Rename preset_id back to preset_name"""
    with db.get_cursor() as cursor:
        # Create old table structure
        cursor.execute("""
            CREATE TABLE generations_old (
                id TEXT PRIMARY KEY,
                preset_name TEXT NOT NULL,
                preset_version TEXT,
                form_data TEXT NOT NULL,
                user_id TEXT,
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
        
        # Copy data back
        cursor.execute("""
            INSERT INTO generations_old (
                id, preset_name, preset_version, form_data, user_id, status, progress,
                current_step, current_step_num, total_steps, error_message, output_directory,
                created_at, started_at, completed_at, updated_at
            )
            SELECT 
                id, preset_id, preset_version, form_data, user_id, status, progress,
                current_step, current_step_num, total_steps, error_message, output_directory,
                created_at, started_at, completed_at, updated_at
            FROM generations
        """)
        
        # Drop current table
        cursor.execute("DROP TABLE generations")
        
        # Rename old table back
        cursor.execute("ALTER TABLE generations_old RENAME TO generations")
        
        # Recreate trigger
        cursor.execute("""
            CREATE TRIGGER update_generations_updated_at 
            AFTER UPDATE ON generations 
            FOR EACH ROW 
            BEGIN 
                UPDATE generations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        # Recreate indexes
        cursor.execute("CREATE INDEX idx_generations_status ON generations (status)")
        cursor.execute("CREATE INDEX idx_generations_created_at ON generations (created_at)")
        cursor.execute("CREATE INDEX idx_generations_user_id ON generations (user_id)")