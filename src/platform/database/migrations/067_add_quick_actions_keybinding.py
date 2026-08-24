"""
Seed a default keybinding for opening the plugin quick-actions palette.

Adds the `open_quick_actions` action (default key `a`, global context) to
`keybinding_defaults` so the sidebar quick-actions fuzzy finder can be opened
from anywhere and shows up in the keyboard-shortcuts help modal.
"""

from src.platform.database.database import db


def up():
    """Insert the open_quick_actions default keybinding."""
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT OR IGNORE INTO keybinding_defaults
                (id, key, modifiers, label, category, context, description, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                'open_quick_actions',
                'a',
                '',
                'Open Quick Actions',
                'general',
                'global',
                'Open the plugin quick-actions palette',
                4,
            ),
        )

        print("Migration 067: Seeded open_quick_actions keybinding default")


def down():
    """Remove the open_quick_actions default keybinding."""
    with db.get_cursor() as cursor:
        cursor.execute("DELETE FROM keybinding_defaults WHERE id = ?", ('open_quick_actions',))

        print("Migration 067: Removed open_quick_actions keybinding default")
