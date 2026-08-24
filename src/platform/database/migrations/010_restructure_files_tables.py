"""
Restructure database to have separate files table and generation_files junction table.
This allows storing different types of files (image, video, avatar, model, etc.) 
and associating them with multiple generations if needed.
"""

from src.platform.database.database import db

def up():
    """Restructure tables: generation_files -> files + generation_files (junction)"""
    with db.get_cursor() as cursor:
        # 1. Create the new files table
        cursor.execute("""
            CREATE TABLE files (
                id TEXT PRIMARY KEY,                    -- ULID primary key
                file_path TEXT NOT NULL,               -- Relative path to the file
                file_type TEXT NOT NULL,               -- Type: 'image', 'video', 'avatar', 'model', 'metadata', etc.
                file_size INTEGER,                     -- File size in bytes
                pipe_name TEXT,                        -- Name of the pipe that generated this file
                is_final BOOLEAN DEFAULT FALSE,        -- Whether this is a final output file
                user_id TEXT,                          -- References users(id) with CASCADE delete
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # 2. Create the new generation_files junction table
        cursor.execute("""
            CREATE TABLE generation_files_new (
                id TEXT PRIMARY KEY,                   -- ULID primary key
                generation_id TEXT NOT NULL,          -- References generations(id) with CASCADE delete
                file_id TEXT NOT NULL,                -- References files(id) with CASCADE delete
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE,
                UNIQUE(generation_id, file_id)        -- Prevent duplicate associations
            )
        """)
        
        # 3. Create indexes for the new tables
        cursor.execute("CREATE INDEX idx_files_file_type ON files (file_type)")
        cursor.execute("CREATE INDEX idx_files_user_id ON files (user_id)")
        cursor.execute("CREATE INDEX idx_files_created_at ON files (created_at)")
        cursor.execute("CREATE INDEX idx_generation_files_new_generation_id ON generation_files_new (generation_id)")
        cursor.execute("CREATE INDEX idx_generation_files_new_file_id ON generation_files_new (file_id)")
        
        # 4. Drop the old generation_files table (no data migration as requested)
        cursor.execute("DROP TABLE generation_files")
        
        # 5. Rename the new junction table
        cursor.execute("ALTER TABLE generation_files_new RENAME TO generation_files")

def down():
    """Revert back to original generation_files table structure"""
    with db.get_cursor() as cursor:
        # Drop the new tables
        cursor.execute("DROP TABLE generation_files")
        cursor.execute("DROP TABLE files")
        
        # Recreate the original generation_files table
        cursor.execute("""
            CREATE TABLE generation_files (
                id TEXT PRIMARY KEY,                  -- ULID primary key
                generation_id TEXT NOT NULL,         -- References generations(id) with CASCADE delete
                file_path TEXT NOT NULL,             -- Relative path to the generated file
                file_type TEXT NOT NULL,             -- Type of file (e.g., 'image', 'metadata')
                user_id TEXT,                        -- References users(id) with CASCADE delete
                file_size INTEGER,                   -- File size in bytes
                pipe_name TEXT,                      -- Name of the pipe that generated this file
                is_final BOOLEAN DEFAULT FALSE,      -- Whether this is a final output file
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        
        # Recreate index
        cursor.execute("CREATE INDEX idx_generation_files_generation_id ON generation_files (generation_id)")