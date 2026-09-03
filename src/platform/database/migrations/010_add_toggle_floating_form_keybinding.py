"""010 adds the `toggle_floating_form` default keybinding (key `q`, "Float
Generation Form") to `keybinding_defaults`.

`001_baseline.py` is a frozen snapshot of the schema/seed data the old
143-file migration chain produced, pinned by
`tests/platform/database/test_migration_001_baseline.py` - new keybindings
are never added there. This migration seeds the row for every database,
fresh install or upgrade alike, since it runs after `001_baseline.py` in
filename order either way.

IDEMPOTENT: `INSERT OR IGNORE` keyed on the `keybinding_defaults` primary key.
"""

from src.platform.database.database import db

_ACTION_ID = "toggle_floating_form"


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT OR IGNORE INTO keybinding_defaults
                (id, key, modifiers, label, category, context, description, enabled, source, sort_order)
            VALUES (?, 'q', '', 'Float Generation Form', 'generation', 'generate',
                    'Show the generation form as a floating overlay', 1, 'system', 14)
            """,
            (_ACTION_ID,),
        )
    print(
        f"Migration 010_add_toggle_floating_form_keybinding: seeded "
        f"'{_ACTION_ID}' default keybinding"
    )


def down():
    with db.get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM keybinding_defaults WHERE id = ?", (_ACTION_ID,)
        )
    print(
        f"Migration 010_add_toggle_floating_form_keybinding: removed "
        f"'{_ACTION_ID}' default keybinding"
    )
