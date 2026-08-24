"""Renumber navigation shortcuts so digits follow the sidebar's visual order."""

from src.platform.database.database import db


def up():
    """Shift Library onto key 3, sliding Models/Autocomplete/Prompts up one digit.

    Only the defaults table is touched - user overrides live in
    user_keybindings and are never affected by a default's key changing.
    """
    with db.get_cursor() as cursor:
        cursor.executemany(
            "UPDATE keybinding_defaults SET key = ?, sort_order = ? WHERE id = ?",
            [
                ('3', 22, 'go_library'),
                ('4', 23, 'go_models'),
                ('5', 24, 'go_autocomplete'),
                ('6', 25, 'go_prompts'),
            ],
        )

        print("Migration 124: Reordered navigation keybinding defaults")


def down():
    """Restore the pre-reorder key/sort_order assignment."""
    with db.get_cursor() as cursor:
        cursor.executemany(
            "UPDATE keybinding_defaults SET key = ?, sort_order = ? WHERE id = ?",
            [
                ('3', 22, 'go_models'),
                ('4', 23, 'go_autocomplete'),
                ('5', 24, 'go_prompts'),
                ('6', 25, 'go_library'),
            ],
        )

        print("Migration 124: Reverted navigation keybinding reorder")
