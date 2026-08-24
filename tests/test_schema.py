"""
Test database schema creation.

This module contains the final database schema for testing purposes.
Instead of running all migrations, we create the final expected schema directly.
"""

def create_test_schema(db):
    """Create test database schema with all tables and test data"""
    with db.get_cursor() as cursor:
        # Create settings table (from migration 013)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
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
        
        # Create user_settings table (from migration 013)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                setting_id TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (setting_id) REFERENCES settings(id) ON DELETE CASCADE
            )
        """)
        
        # Insert default settings (from migration 013)
        cursor.execute("""
            INSERT OR IGNORE INTO settings (id, key, value, value_type, description, type) VALUES
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
        
        # Create other essential tables that tests might need
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password_hash TEXT,
                account_type TEXT NOT NULL CHECK (account_type IN ('USER', 'ADMIN')) DEFAULT 'USER',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            )
        """)
        
        # Create generations table for any generation-related tests
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS generations (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
                progress REAL DEFAULT 0.0,
                preset_id TEXT,
                backend_name TEXT,
                user_id TEXT,
                current_step TEXT,
                current_step_num INTEGER DEFAULT 0,
                total_steps INTEGER DEFAULT 0,
                error_message TEXT,
                output_directory TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Create files table for generation outputs
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL CHECK (file_type IN ('IMAGE', 'VIDEO')),
                user_id TEXT NOT NULL,
                mime_type TEXT,
                file_size INTEGER,
                pipe_name TEXT,
                is_final INTEGER DEFAULT 0,
                thumbnail_small TEXT,
                thumbnail_medium TEXT,
                thumbnail_large TEXT,
                width INTEGER,
                height INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Create generation_files junction table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS generation_files (
                id TEXT PRIMARY KEY,
                generation_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            )
        """)