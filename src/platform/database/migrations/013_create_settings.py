"""
Create settings and user_settings tables for database-backed configuration management
"""

from src.platform.database.database import db

def up():
    """Create settings and user_settings tables"""
    with db.get_cursor() as cursor:
        # Create settings table (core settings with system/user types)
        cursor.execute("""
            CREATE TABLE settings (
                id TEXT PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                value_type TEXT NOT NULL CHECK (value_type IN ('string', 'integer', 'float', 'boolean', 'json')),
                description TEXT,
                type TEXT NOT NULL CHECK (type IN ('USER', 'SYSTEM')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create user_settings relation table (user-specific setting overrides)
        cursor.execute("""
            CREATE TABLE user_settings (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                setting_id TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (setting_id) REFERENCES settings (id) ON DELETE CASCADE,
                UNIQUE(user_id, setting_id)
            )
        """)
        
        # Create indexes for settings table
        cursor.execute("CREATE INDEX idx_settings_key ON settings (key)")
        cursor.execute("CREATE INDEX idx_settings_type ON settings (type)")
        cursor.execute("CREATE INDEX idx_settings_created_at ON settings (created_at)")
        
        # Create indexes for user_settings table
        cursor.execute("CREATE INDEX idx_user_settings_user_id ON user_settings (user_id)")
        cursor.execute("CREATE INDEX idx_user_settings_setting_id ON user_settings (setting_id)")
        cursor.execute("CREATE INDEX idx_user_settings_created_at ON user_settings (created_at)")
        
        # Create triggers to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_settings_updated_at 
            AFTER UPDATE ON settings 
            FOR EACH ROW 
            BEGIN 
                UPDATE settings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER update_user_settings_updated_at 
            AFTER UPDATE ON user_settings 
            FOR EACH ROW 
            BEGIN 
                UPDATE user_settings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        # Insert default system settings based on existing SettingsManager defaults
        cursor.execute("""
            INSERT INTO settings (id, key, value, value_type, description, type) VALUES
            ('setting_hf_api_key', 'hf_api_key', '', 'string', 'Hugging Face API key for model downloads', 'SYSTEM'),
            ('setting_civitai_api_key', 'civitai_api_key', '', 'string', 'CivitAI API key for model downloads', 'SYSTEM'),
            ('setting_models_dir', 'models_dir', 'models', 'string', 'Directory path for storing models', 'SYSTEM'),
            ('setting_cache_dir', 'cache_dir', 'cache', 'string', 'Directory path for caching temporary files', 'SYSTEM'),
            ('setting_device', 'device', 'cuda', 'string', 'Compute device for model execution (cuda/cpu)', 'SYSTEM'),
            ('setting_precision', 'precision', 'fp16', 'string', 'Model precision (fp16/fp32/bf16)', 'SYSTEM'),
            ('setting_nsfw_filter', 'nsfw_filter', 'false', 'boolean', 'Allow NSFW content generation', 'USER'),
            ('setting_dtype', 'dtype', 'float16', 'string', 'Data type for model tensors', 'SYSTEM'),
            ('setting_output_directory', 'output_directory', 'outputs', 'string', 'Directory path for storing generated outputs', 'SYSTEM')
        """)


def down():
    """Drop settings and user_settings tables"""
    with db.get_cursor() as cursor:
        # Drop triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_user_settings_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_settings_updated_at")
        
        # Drop indexes for user_settings
        cursor.execute("DROP INDEX IF EXISTS idx_user_settings_created_at")
        cursor.execute("DROP INDEX IF EXISTS idx_user_settings_setting_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_settings_user_id")
        
        # Drop indexes for settings
        cursor.execute("DROP INDEX IF EXISTS idx_settings_created_at")
        cursor.execute("DROP INDEX IF EXISTS idx_settings_type")
        cursor.execute("DROP INDEX IF EXISTS idx_settings_key")
        
        # Drop tables (user_settings first due to foreign key)
        cursor.execute("DROP TABLE IF EXISTS user_settings")
        cursor.execute("DROP TABLE IF EXISTS settings")