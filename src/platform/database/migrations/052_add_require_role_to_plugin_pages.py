"""
Migration 052: Add require_role column to plugin_pages table
Adds optional require_role field to restrict plugin pages to specific user roles.
"""

from src.platform.database.database import db


def up():
    """Add require_role column to plugin_pages"""
    with db.get_cursor() as cursor:
        # Check if column already exists (idempotent)
        cursor.execute("PRAGMA table_info(plugin_pages)")
        columns = [row['name'] for row in cursor.fetchall()]

        if 'require_role' not in columns:
            cursor.execute("""
                ALTER TABLE plugin_pages ADD COLUMN require_role TEXT
            """)
            print("Migration 052: Added require_role column to plugin_pages")
        else:
            print("Migration 052: require_role column already exists, skipping")


def down():
    """Remove require_role column from plugin_pages (SQLite limitation: recreate table)"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE plugin_pages_backup AS
            SELECT id, plugin_id, route, component_path, label,
                   icon_svg, sidebar_order, show_in_sidebar, created_at
            FROM plugin_pages
        """)
        cursor.execute("DROP TABLE plugin_pages")
        cursor.execute("""
            CREATE TABLE plugin_pages (
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
        cursor.execute("""
            INSERT INTO plugin_pages (id, plugin_id, route, component_path, label,
                                      icon_svg, sidebar_order, show_in_sidebar, created_at)
            SELECT id, plugin_id, route, component_path, label,
                   icon_svg, sidebar_order, show_in_sidebar, created_at
            FROM plugin_pages_backup
        """)
        cursor.execute("DROP TABLE plugin_pages_backup")
        print("Migration 052: Reverted require_role column from plugin_pages")
