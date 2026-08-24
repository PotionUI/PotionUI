"""
Migration 048: Create plugin_pages table
Creates the plugin_pages table for storing plugin page registrations.
"""

from src.platform.database.database import db


def up():
    """Create plugin_pages table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS plugin_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id TEXT NOT NULL,
                route TEXT NOT NULL UNIQUE,
                component_path TEXT NOT NULL,
                label TEXT NOT NULL,
                icon_svg TEXT,
                sidebar_order INTEGER DEFAULT 100,
                show_in_sidebar BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (plugin_id) REFERENCES plugins(id) ON DELETE CASCADE
            )
        """)

        print("Migration 048: Created plugin_pages table")


def down():
    """Drop plugin_pages table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS plugin_pages")

        print("Migration 048: Reverted plugin_pages table")
