"""Seed global navigation shortcuts for Autocomplete and Prompts."""

from src.platform.database.database import db


def up():
    """Add the fourth and fifth core navigation actions without replacing overrides."""
    with db.get_cursor() as cursor:
        cursor.executemany(
            """
            INSERT OR IGNORE INTO keybinding_defaults
                (id, key, modifiers, label, category, context, description, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    'go_autocomplete',
                    '4',
                    '',
                    'Go to Autocomplete',
                    'navigation',
                    'global',
                    'Navigate to Autocomplete page',
                    23,
                ),
                (
                    'go_prompts',
                    '5',
                    '',
                    'Go to Prompts',
                    'navigation',
                    'global',
                    'Navigate to Prompts page',
                    24,
                ),
            ],
        )

        print("Migration 106: Seeded Autocomplete and Prompts keybinding defaults")


def down():
    """Remove the two navigation defaults."""
    with db.get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM keybinding_defaults WHERE id IN (?, ?)",
            ('go_autocomplete', 'go_prompts'),
        )

        print("Migration 106: Removed Autocomplete and Prompts keybinding defaults")
