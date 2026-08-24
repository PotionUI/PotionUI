"""
Convert INT autoincrement IDs to ULID for consistency
"""

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid

def up():
    """Convert INT autoincrement tables to use ULID"""
    with db.get_cursor() as cursor:
        # Step 1: Create new configurations table with ULID
        cursor.execute("""
            CREATE TABLE configurations_new (
                id TEXT PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                value_type TEXT NOT NULL DEFAULT 'string',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Step 2: Migrate existing configurations data
        cursor.execute("SELECT * FROM configurations ORDER BY id")
        existing_configs = cursor.fetchall()
        
        for config in existing_configs:
            new_id = generate_ulid()
            cursor.execute("""
                INSERT INTO configurations_new 
                (id, key, value, value_type, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (new_id, config[1], config[2], config[3], config[4], config[5], config[6]))
        
        # Step 3: Replace old table with new table
        cursor.execute("DROP TRIGGER IF EXISTS update_configurations_updated_at")
        cursor.execute("DROP TABLE configurations")
        cursor.execute("ALTER TABLE configurations_new RENAME TO configurations")
        
        # Step 4: Recreate trigger for new table
        cursor.execute("""
            CREATE TRIGGER update_configurations_updated_at 
            AFTER UPDATE ON configurations 
            FOR EACH ROW 
            BEGIN 
                UPDATE configurations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        # Step 5: Create new generation_files table with ULID
        cursor.execute("""
            CREATE TABLE generation_files_new (
                id TEXT PRIMARY KEY,
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
        
        # Step 6: Migrate existing generation_files data
        cursor.execute("SELECT * FROM generation_files ORDER BY id")
        existing_files = cursor.fetchall()
        
        for file_record in existing_files:
            new_id = generate_ulid()
            cursor.execute("""
                INSERT INTO generation_files_new 
                (id, generation_id, file_path, file_type, file_size, pipe_name, is_final, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (new_id, file_record[1], file_record[2], file_record[3], 
                  file_record[4], file_record[5], file_record[6], file_record[7]))
        
        # Step 7: Replace old table with new table
        cursor.execute("DROP INDEX IF EXISTS idx_generation_files_generation_id")
        cursor.execute("DROP TABLE generation_files")
        cursor.execute("ALTER TABLE generation_files_new RENAME TO generation_files")
        
        # Step 8: Recreate index for new table
        cursor.execute("CREATE INDEX idx_generation_files_generation_id ON generation_files (generation_id)")

def down():
    """Revert back to INT autoincrement IDs"""
    with db.get_cursor() as cursor:
        # Step 1: Create old configurations table with INT autoincrement
        cursor.execute("""
            CREATE TABLE configurations_old (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                value_type TEXT NOT NULL DEFAULT 'string',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Step 2: Migrate configurations data back (losing ULID IDs)
        cursor.execute("SELECT key, value, value_type, description, created_at, updated_at FROM configurations")
        existing_configs = cursor.fetchall()
        
        for config in existing_configs:
            cursor.execute("""
                INSERT INTO configurations_old 
                (key, value, value_type, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, config)
        
        # Step 3: Replace new table with old table
        cursor.execute("DROP TRIGGER IF EXISTS update_configurations_updated_at")
        cursor.execute("DROP TABLE configurations")
        cursor.execute("ALTER TABLE configurations_old RENAME TO configurations")
        
        # Step 4: Recreate trigger for old table
        cursor.execute("""
            CREATE TRIGGER update_configurations_updated_at 
            AFTER UPDATE ON configurations 
            FOR EACH ROW 
            BEGIN 
                UPDATE configurations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        # Step 5: Create old generation_files table with INT autoincrement
        cursor.execute("""
            CREATE TABLE generation_files_old (
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
        
        # Step 6: Migrate generation_files data back (losing ULID IDs)
        cursor.execute("""
            SELECT generation_id, file_path, file_type, file_size, pipe_name, is_final, created_at 
            FROM generation_files
        """)
        existing_files = cursor.fetchall()
        
        for file_record in existing_files:
            cursor.execute("""
                INSERT INTO generation_files_old 
                (generation_id, file_path, file_type, file_size, pipe_name, is_final, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, file_record)
        
        # Step 7: Replace new table with old table
        cursor.execute("DROP INDEX IF EXISTS idx_generation_files_generation_id")
        cursor.execute("DROP TABLE generation_files")
        cursor.execute("ALTER TABLE generation_files_old RENAME TO generation_files")
        
        # Step 8: Recreate index for old table
        cursor.execute("CREATE INDEX idx_generation_files_generation_id ON generation_files (generation_id)")