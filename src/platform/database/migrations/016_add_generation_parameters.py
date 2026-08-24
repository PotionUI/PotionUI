"""
Add generation parameters table to store parameters associated with generated files.
This allows storing parameters like seed, cfg, steps etc. for each generated image.
"""

from src.platform.database.database import db

def up():
    """Create generation_parameters table"""
    with db.get_cursor() as cursor:
        # Create the generation_parameters table
        cursor.execute("""
            CREATE TABLE generation_parameters (
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
        
        # Create indexes for efficient querying
        cursor.execute("CREATE INDEX idx_generation_parameters_generation_id ON generation_parameters (generation_id)")
        cursor.execute("CREATE INDEX idx_generation_parameters_name ON generation_parameters (parameter_name)")
        cursor.execute("CREATE INDEX idx_generation_parameters_index ON generation_parameters (parameter_index)")

def down():
    """Drop generation_parameters table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE generation_parameters")