"""
Create user_groups, user_group_members, user_group_presets, and user_group_llms tables
for managing group-based access control to presets and LLM configurations.
"""

from src.platform.database.database import db

def up():
    """Create user groups tables"""
    with db.get_cursor() as cursor:
        # Create user_groups table
        cursor.execute("""
            CREATE TABLE user_groups (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("CREATE INDEX idx_user_groups_name ON user_groups (name)")

        cursor.execute("""
            CREATE TRIGGER update_user_groups_updated_at
            AFTER UPDATE ON user_groups
            FOR EACH ROW
            BEGIN
                UPDATE user_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        # Create user_group_members table (users <-> groups)
        cursor.execute("""
            CREATE TABLE user_group_members (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(group_id, user_id)
            )
        """)

        cursor.execute("CREATE INDEX idx_user_group_members_group_id ON user_group_members (group_id)")
        cursor.execute("CREATE INDEX idx_user_group_members_user_id ON user_group_members (user_id)")

        cursor.execute("""
            CREATE TRIGGER update_user_group_members_updated_at
            AFTER UPDATE ON user_group_members
            FOR EACH ROW
            BEGIN
                UPDATE user_group_members SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        # Create user_group_presets table (groups <-> presets)
        cursor.execute("""
            CREATE TABLE user_group_presets (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                preset_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
                FOREIGN KEY (preset_id) REFERENCES presets(id) ON DELETE CASCADE,
                UNIQUE(group_id, preset_id)
            )
        """)

        cursor.execute("CREATE INDEX idx_user_group_presets_group_id ON user_group_presets (group_id)")
        cursor.execute("CREATE INDEX idx_user_group_presets_preset_id ON user_group_presets (preset_id)")

        cursor.execute("""
            CREATE TRIGGER update_user_group_presets_updated_at
            AFTER UPDATE ON user_group_presets
            FOR EACH ROW
            BEGIN
                UPDATE user_group_presets SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        # Create user_group_llms table (groups <-> LLM configurations)
        cursor.execute("""
            CREATE TABLE user_group_llms (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                llm_config_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
                FOREIGN KEY (llm_config_id) REFERENCES llm_configurations(id) ON DELETE CASCADE,
                UNIQUE(group_id, llm_config_id)
            )
        """)

        cursor.execute("CREATE INDEX idx_user_group_llms_group_id ON user_group_llms (group_id)")
        cursor.execute("CREATE INDEX idx_user_group_llms_llm_config_id ON user_group_llms (llm_config_id)")

        cursor.execute("""
            CREATE TRIGGER update_user_group_llms_updated_at
            AFTER UPDATE ON user_group_llms
            FOR EACH ROW
            BEGIN
                UPDATE user_group_llms SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

def down():
    """Drop user groups tables"""
    with db.get_cursor() as cursor:
        # Drop triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_user_group_llms_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_user_group_presets_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_user_group_members_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_user_groups_updated_at")

        # Drop indexes
        cursor.execute("DROP INDEX IF EXISTS idx_user_group_llms_llm_config_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_group_llms_group_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_group_presets_preset_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_group_presets_group_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_group_members_user_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_group_members_group_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_groups_name")

        # Drop tables (order matters due to foreign keys)
        cursor.execute("DROP TABLE IF EXISTS user_group_llms")
        cursor.execute("DROP TABLE IF EXISTS user_group_presets")
        cursor.execute("DROP TABLE IF EXISTS user_group_members")
        cursor.execute("DROP TABLE IF EXISTS user_groups")
