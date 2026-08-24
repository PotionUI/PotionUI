"""Seed a default keybinding for folding the generate page's left form panel."""

from src.platform.database.database import db


def up():
    """Insert the toggle_left_panel default keybinding."""
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT OR IGNORE INTO keybinding_defaults
                (id, key, modifiers, label, category, context, description, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'toggle_left_panel',
                'f',
                '',
                'Toggle Form Panel',
                'generation',
                'generate',
                'Fold or unfold the left generation form panel',
                13,
            ),
        )

        print("Migration 113: Seeded toggle_left_panel keybinding default")


def down():
    """Remove the toggle_left_panel default keybinding."""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM keybinding_defaults WHERE id = ?", ('toggle_left_panel',))

        print("Migration 113: Removed toggle_left_panel keybinding default")
