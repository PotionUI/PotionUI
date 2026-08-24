"""Seed the global navigation shortcut for Inspirations."""

from src.platform.database.database import db


def up():
    """Add the seventh core navigation action without replacing overrides."""
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT OR IGNORE INTO keybinding_defaults
                (id, key, modifiers, label, category, context, description, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'go_inspirations',
                '7',
                '',
                'Go to Inspirations',
                'navigation',
                'global',
                'Navigate to Inspirations page',
                26,
            ),
        )

        print("Migration 138: Seeded Inspirations keybinding default")


def down():
    """Remove the navigation default."""
    with db.get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM keybinding_defaults WHERE id = ?",
            ('go_inspirations',),
        )

        print("Migration 138: Removed Inspirations keybinding default")
