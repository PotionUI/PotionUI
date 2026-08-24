"""
Create presets and user_presets tables for tracking installed presets and user assignments
"""

from src.platform.database.database import db

def up():
    """Create presets and user_presets tables"""
    with db.get_cursor() as cursor:
        # Create presets table (tracks which presets are installed by admin)
        cursor.execute("""
            CREATE TABLE presets (
                id TEXT PRIMARY KEY,
                preset_id TEXT UNIQUE NOT NULL,
                installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create user_presets relation table (tracks which users have access to which presets)
        cursor.execute("""
            CREATE TABLE user_presets (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                preset_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (preset_id) REFERENCES presets (id) ON DELETE CASCADE,
                UNIQUE(user_id, preset_id)
            )
        """)
        
        # Create indexes for presets table
        cursor.execute("CREATE INDEX idx_presets_preset_id ON presets (preset_id)")
        cursor.execute("CREATE INDEX idx_presets_installed_at ON presets (installed_at)")
        
        # Create indexes for user_presets table
        cursor.execute("CREATE INDEX idx_user_presets_user_id ON user_presets (user_id)")
        cursor.execute("CREATE INDEX idx_user_presets_preset_id ON user_presets (preset_id)")
        cursor.execute("CREATE INDEX idx_user_presets_assigned_at ON user_presets (assigned_at)")
        
        # Create triggers to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_presets_updated_at 
            AFTER UPDATE ON presets 
            FOR EACH ROW 
            BEGIN 
                UPDATE presets SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER update_user_presets_updated_at 
            AFTER UPDATE ON user_presets 
            FOR EACH ROW 
            BEGIN 
                UPDATE user_presets SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

def down():
    """Drop presets and user_presets tables"""
    with db.get_cursor() as cursor:
        # Drop triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_user_presets_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_presets_updated_at")
        
        # Drop indexes for user_presets
        cursor.execute("DROP INDEX IF EXISTS idx_user_presets_assigned_at")
        cursor.execute("DROP INDEX IF EXISTS idx_user_presets_preset_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_presets_user_id")
        
        # Drop indexes for presets
        cursor.execute("DROP INDEX IF EXISTS idx_presets_installed_at")
        cursor.execute("DROP INDEX IF EXISTS idx_presets_preset_id")
        
        # Drop tables (user_presets first due to foreign key)
        cursor.execute("DROP TABLE IF EXISTS user_presets")
        cursor.execute("DROP TABLE IF EXISTS presets")