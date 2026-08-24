"""
Create LLM tables (configurations, commands, prompt styles)
"""

from src.platform.database.database import db

def up():
    """Create LLM tables"""
    with db.get_cursor() as cursor:
        # Create LLM configurations table
        cursor.execute("""
            CREATE TABLE llm_configurations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                base_url TEXT NOT NULL,
                api_key TEXT,
                model TEXT NOT NULL,
                system_message TEXT NOT NULL,
                temperature REAL NOT NULL DEFAULT 0.7,
                max_tokens INTEGER NOT NULL DEFAULT 1000,
                timeout INTEGER NOT NULL DEFAULT 30,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create LLM commands table
        cursor.execute("""
            CREATE TABLE llm_commands (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                prompt TEXT NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create LLM prompt styles table
        cursor.execute("""
            CREATE TABLE llm_prompt_styles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                suffix TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create triggers to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_llm_configurations_updated_at 
            AFTER UPDATE ON llm_configurations 
            FOR EACH ROW 
            BEGIN 
                UPDATE llm_configurations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER update_llm_commands_updated_at 
            AFTER UPDATE ON llm_commands 
            FOR EACH ROW 
            BEGIN 
                UPDATE llm_commands SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        cursor.execute("""
            CREATE TRIGGER update_llm_prompt_styles_updated_at 
            AFTER UPDATE ON llm_prompt_styles 
            FOR EACH ROW 
            BEGIN 
                UPDATE llm_prompt_styles SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)
        
        # Create indices for performance
        cursor.execute("CREATE INDEX idx_llm_configurations_enabled ON llm_configurations(enabled)")
        cursor.execute("CREATE INDEX idx_llm_commands_enabled ON llm_commands(enabled)")

def down():
    """Drop LLM tables"""
    with db.get_cursor() as cursor:
        # Drop triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_llm_configurations_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_llm_commands_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_llm_prompt_styles_updated_at")
        
        # Drop indices
        cursor.execute("DROP INDEX IF EXISTS idx_llm_configurations_enabled")
        cursor.execute("DROP INDEX IF EXISTS idx_llm_commands_enabled")
        
        # Drop tables
        cursor.execute("DROP TABLE IF EXISTS llm_prompt_styles")
        cursor.execute("DROP TABLE IF EXISTS llm_commands")
        cursor.execute("DROP TABLE IF EXISTS llm_configurations")