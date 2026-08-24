"""
Update generation_parameters table to use generation_id instead of generation_file_id.
This migration alters the existing table structure to support the new parameter storage approach.
"""

from src.platform.database.database import db

def up():
    """Update generation_parameters table schema"""
    with db.get_cursor() as cursor:
        # Check if the table exists and has the old schema
        cursor.execute("PRAGMA table_info(generation_parameters)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'generation_file_id' in columns and 'generation_id' not in columns:
            # Table has old schema, need to migrate
            
            # Create new table with correct schema
            cursor.execute("""
                CREATE TABLE generation_parameters_new (
                    id TEXT PRIMARY KEY,                    -- ULID primary key
                    generation_id TEXT NOT NULL,            -- References generations(id)
                    parameter_name TEXT NOT NULL,           -- Name of the parameter (e.g., 'seed', 'cfg')
                    parameter_value TEXT NOT NULL,          -- JSON-encoded value
                    parameter_index INTEGER DEFAULT 0,      -- Index of the generated image (0, 1, 2, etc.)
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                    UNIQUE(generation_id, parameter_name, parameter_index)
                )
            """)
            
            # Copy existing data if any (transform generation_file_id to generation_id)
            # This is complex because we need to join through generation_files to get generation_id
            # For now, we'll just drop the old data since this is a new feature
            
            # Drop old table
            cursor.execute("DROP TABLE generation_parameters")
            
            # Rename new table
            cursor.execute("ALTER TABLE generation_parameters_new RENAME TO generation_parameters")
            
            # Create indexes for the new table
            cursor.execute("CREATE INDEX idx_generation_parameters_generation_id ON generation_parameters (generation_id)")
            cursor.execute("CREATE INDEX idx_generation_parameters_name ON generation_parameters (parameter_name)")
            cursor.execute("CREATE INDEX idx_generation_parameters_index ON generation_parameters (parameter_index)")

def down():
    """Revert generation_parameters table to old schema"""
    with db.get_cursor() as cursor:
        # Drop current table
        cursor.execute("DROP TABLE generation_parameters")
        
        # Recreate old table structure
        cursor.execute("""
            CREATE TABLE generation_parameters (
                id TEXT PRIMARY KEY,                    -- ULID primary key
                generation_file_id TEXT NOT NULL,       -- References generation_files(id)
                parameter_name TEXT NOT NULL,           -- Name of the parameter (e.g., 'seed', 'cfg')
                parameter_value TEXT NOT NULL,          -- JSON-encoded value
                parameter_index INTEGER DEFAULT 0,      -- Index when multiple values (for batch generation)
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_file_id) REFERENCES generation_files(id) ON DELETE CASCADE,
                UNIQUE(generation_file_id, parameter_name, parameter_index)
            )
        """)
        
        # Create old indexes
        cursor.execute("CREATE INDEX idx_generation_parameters_generation_file_id ON generation_parameters (generation_file_id)")
        cursor.execute("CREATE INDEX idx_generation_parameters_name ON generation_parameters (parameter_name)")