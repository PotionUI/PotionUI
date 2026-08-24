"""
Create configurations table
"""

from src.platform.database.database import db

def up():
    """Create configurations table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE configurations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                value_type TEXT NOT NULL DEFAULT 'string',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create trigger to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_configurations_updated_at 
            AFTER UPDATE ON configurations 
            FOR EACH ROW 
            BEGIN 
                UPDATE configurations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

def down():
    """Drop configurations table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TRIGGER IF EXISTS update_configurations_updated_at")
        cursor.execute("DROP TABLE IF EXISTS configurations")