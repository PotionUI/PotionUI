"""Seed the global navigation shortcut for Library."""

from src.platform.database.database import db


def up():
    """Add the sixth core navigation action without replacing overrides."""
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT OR IGNORE INTO keybinding_defaults
                (id, key, modifiers, label, category, context, description, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'go_library',
                '6',
                '',
                'Go to Library',
                'navigation',
                'global',
                'Navigate to Library page',
                25,
            ),
        )

        print("Migration 123: Seeded Library keybinding default")


def down():
    """Remove the navigation default."""
    with db.get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM keybinding_defaults WHERE id = ?",
            ('go_library',),
        )

        print("Migration 123: Removed Library keybinding default")
