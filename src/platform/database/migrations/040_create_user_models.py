"""
Create user_models and user_group_models tables
for managing user/group-based model access control.
"""

from src.platform.database.database import db

def up():
    """Create user model assignment tables"""
    with db.get_cursor() as cursor:
        # Create user_models table (direct user-model assignments)
        cursor.execute("""
            CREATE TABLE user_models (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,
                UNIQUE(user_id, model_id)
            )
        """)

        cursor.execute("CREATE INDEX idx_user_models_user_id ON user_models (user_id)")
        cursor.execute("CREATE INDEX idx_user_models_model_id ON user_models (model_id)")

        cursor.execute("""
            CREATE TRIGGER update_user_models_updated_at
            AFTER UPDATE ON user_models
            FOR EACH ROW
            BEGIN
                UPDATE user_models SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        # Create user_group_models table (group-model assignments)
        cursor.execute("""
            CREATE TABLE user_group_models (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES user_groups(id) ON DELETE CASCADE,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,
                UNIQUE(group_id, model_id)
            )
        """)

        cursor.execute("CREATE INDEX idx_user_group_models_group_id ON user_group_models (group_id)")
        cursor.execute("CREATE INDEX idx_user_group_models_model_id ON user_group_models (model_id)")

        cursor.execute("""
            CREATE TRIGGER update_user_group_models_updated_at
            AFTER UPDATE ON user_group_models
            FOR EACH ROW
            BEGIN
                UPDATE user_group_models SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

def down():
    """Drop user model assignment tables"""
    with db.get_cursor() as cursor:
        # Drop triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_user_group_models_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_user_models_updated_at")

        # Drop indexes
        cursor.execute("DROP INDEX IF EXISTS idx_user_group_models_model_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_group_models_group_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_models_model_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_models_user_id")

        # Drop tables
        cursor.execute("DROP TABLE IF EXISTS user_group_models")
        cursor.execute("DROP TABLE IF EXISTS user_models")
