"""
Migration 046: Create keybindings tables
Creates keybinding_defaults and user_keybindings tables for the keybinding system.
"""

from src.platform.database.database import db


def up():
    """Create keybinding tables and seed defaults"""
    with db.get_cursor() as cursor:
        # Create keybinding_defaults table
        cursor.execute("""
            CREATE TABLE keybinding_defaults (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL,
                modifiers TEXT DEFAULT '',
                label TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                context TEXT DEFAULT 'global',
                description TEXT,
                enabled INTEGER DEFAULT 1,
                source TEXT DEFAULT 'system',
                sort_order INTEGER DEFAULT 0
            )
        """)

        # Create user_keybindings table
        cursor.execute("""
            CREATE TABLE user_keybindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                key TEXT,
                modifiers TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (action_id) REFERENCES keybinding_defaults(id) ON DELETE CASCADE,
                UNIQUE(user_id, action_id)
            )
        """)

        # Create index for user_keybindings
        cursor.execute("CREATE INDEX idx_user_keybindings_user_id ON user_keybindings(user_id)")

        # Seed default keybindings
        defaults = [
            ('show_help', '?', '', 'Show Keyboard Shortcuts', 'general', 'global', 'Display all available keyboard shortcuts', 0),
            ('open_chat', 'c', '', 'Open AI Chat', 'general', 'global', 'Toggle the AI chat panel', 1),
            ('start_generation', 'g', '', 'Start Generation', 'generation', 'generate', 'Start image generation', 10),
            ('quick_search', '/', '', 'Quick Search', 'general', 'global', 'Open quick search dialog', 2),
            ('go_generate', '1', '', 'Go to Generate', 'navigation', 'global', 'Navigate to Generate page', 20),
            ('go_history', '2', '', 'Go to History', 'navigation', 'global', 'Navigate to History page', 21),
            ('go_models', '3', '', 'Go to Models', 'navigation', 'global', 'Navigate to Models page', 22),
            ('new_tab', 't', '', 'New Tab', 'generation', 'generate', 'Open a new generation tab', 11),
            ('close_tab', 'x', '', 'Close Tab', 'generation', 'generate', 'Close current generation tab', 12),
            ('toggle_sidebar', 'b', '', 'Toggle Sidebar', 'general', 'global', 'Show or hide the sidebar', 3),
        ]

        cursor.executemany("""
            INSERT INTO keybinding_defaults (id, key, modifiers, label, category, context, description, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, defaults)

        print("Migration 046: Created keybinding tables and seeded defaults")


def down():
    """Drop keybinding tables"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS user_keybindings")
        cursor.execute("DROP TABLE IF EXISTS keybinding_defaults")

        print("Migration 046: Reverted keybinding tables")
