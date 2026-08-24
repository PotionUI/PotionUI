"""
Create users table for authentication
"""

from src.platform.database.database import db

def up():
    """Create users table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                account_type TEXT NOT NULL DEFAULT 'USER',
                last_login TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CHECK (account_type IN ('USER', 'ADMIN'))
            )
        """)
        
        # Create indexes
        cursor.execute("CREATE INDEX idx_users_email ON users (email)")
        cursor.execute("CREATE INDEX idx_users_username ON users (username)")
        
        # Create trigger to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_users_updated_at 
            AFTER UPDATE ON users 
            FOR EACH ROW 
            BEGIN 
                UPDATE users SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

def down():
    """Drop users table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TRIGGER IF EXISTS update_users_updated_at")
        cursor.execute("DROP INDEX IF EXISTS idx_users_username")
        cursor.execute("DROP INDEX IF EXISTS idx_users_email")
        cursor.execute("DROP TABLE IF EXISTS users")