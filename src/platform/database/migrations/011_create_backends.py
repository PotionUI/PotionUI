"""
Create backends table
"""

from src.platform.database.database import db

def up():
    """Create backends table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE backends (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                is_default INTEGER NOT NULL DEFAULT 0,
                config TEXT NOT NULL DEFAULT '{}',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create trigger to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_backends_updated_at 
            AFTER UPDATE ON backends 
            FOR EACH ROW 
            BEGIN 
                UPDATE backends SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        # Create unique index to ensure only one default backend
        cursor.execute("""
            CREATE UNIQUE INDEX idx_backends_default 
            ON backends (is_default) 
            WHERE is_default = 1
        """)

def down():
    """Drop backends table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_backends_default")
        cursor.execute("DROP TRIGGER IF EXISTS update_backends_updated_at")
        cursor.execute("DROP TABLE IF EXISTS backends")