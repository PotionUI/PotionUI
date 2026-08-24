"""
Create plugins, plugin_settings, and plugin_hooks tables
for managing the plugin system in PotionUI.
"""

from src.platform.database.database import db


def up():
    """Create plugin system tables"""
    with db.get_cursor() as cursor:
        # Create plugins table
        cursor.execute("""
            CREATE TABLE plugins (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'backend-only',
                description TEXT,
                author TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                manifest_path TEXT NOT NULL,
                installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CHECK (type IN ('frontend-only', 'backend-only', 'full-stack'))
            )
        """)

        cursor.execute("CREATE INDEX idx_plugins_name ON plugins (name)")
        cursor.execute("CREATE INDEX idx_plugins_type ON plugins (type)")
        cursor.execute("CREATE INDEX idx_plugins_enabled ON plugins (enabled)")

        cursor.execute("""
            CREATE TRIGGER update_plugins_updated_at
            AFTER UPDATE ON plugins
            FOR EACH ROW
            BEGIN
                UPDATE plugins SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        # Create plugin_settings table
        cursor.execute("""
            CREATE TABLE plugin_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id TEXT NOT NULL,
                user_id TEXT,
                setting_key TEXT NOT NULL,
                setting_value TEXT,
                is_secret INTEGER DEFAULT 0,
                FOREIGN KEY (plugin_id) REFERENCES plugins(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(plugin_id, user_id, setting_key)
            )
        """)

        cursor.execute("CREATE INDEX idx_plugin_settings_plugin_id ON plugin_settings (plugin_id)")
        cursor.execute("CREATE INDEX idx_plugin_settings_user_id ON plugin_settings (user_id)")
        cursor.execute("CREATE INDEX idx_plugin_settings_key ON plugin_settings (setting_key)")

        # Create plugin_hooks table
        cursor.execute("""
            CREATE TABLE plugin_hooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id TEXT NOT NULL,
                hook_name TEXT NOT NULL,
                hook_type TEXT NOT NULL,
                handler_path TEXT,
                component_path TEXT,
                position TEXT,
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY (plugin_id) REFERENCES plugins(id) ON DELETE CASCADE,
                CHECK (hook_type IN ('backend', 'frontend'))
            )
        """)

        cursor.execute("CREATE INDEX idx_plugin_hooks_plugin_id ON plugin_hooks (plugin_id)")
        cursor.execute("CREATE INDEX idx_plugin_hooks_hook_name ON plugin_hooks (hook_name)")
        cursor.execute("CREATE INDEX idx_plugin_hooks_hook_type ON plugin_hooks (hook_type)")


def down():
    """Drop plugin system tables"""
    with db.get_cursor() as cursor:
        # Drop triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_plugins_updated_at")

        # Drop indexes
        cursor.execute("DROP INDEX IF EXISTS idx_plugin_hooks_hook_type")
        cursor.execute("DROP INDEX IF EXISTS idx_plugin_hooks_hook_name")
        cursor.execute("DROP INDEX IF EXISTS idx_plugin_hooks_plugin_id")
        cursor.execute("DROP INDEX IF EXISTS idx_plugin_settings_key")
        cursor.execute("DROP INDEX IF EXISTS idx_plugin_settings_user_id")
        cursor.execute("DROP INDEX IF EXISTS idx_plugin_settings_plugin_id")
        cursor.execute("DROP INDEX IF EXISTS idx_plugins_enabled")
        cursor.execute("DROP INDEX IF EXISTS idx_plugins_type")
        cursor.execute("DROP INDEX IF EXISTS idx_plugins_name")

        # Drop tables (order matters due to foreign keys)
        cursor.execute("DROP TABLE IF EXISTS plugin_hooks")
        cursor.execute("DROP TABLE IF EXISTS plugin_settings")
        cursor.execute("DROP TABLE IF EXISTS plugins")
