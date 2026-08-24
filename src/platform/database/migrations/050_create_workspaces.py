"""
Migration 050: Create workspaces table
Creates the workspaces table for storing tab layouts (names, colors, order, preset/mode per tab).
"""

from src.platform.database.database import db


def up():
    """Create workspaces table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_workspaces_user
            ON workspaces(user_id)
        """)

        print("Migration 050: Created workspaces table")


def down():
    """Drop workspaces table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS workspaces")

        print("Migration 050: Reverted workspaces table")
