from src.platform.database.database import db

_ACTION_ID = "delete_current_generation"

def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            """
            INSERT OR IGNORE INTO keybinding_defaults
                (id, key, modifiers, label, category, context, description, enabled, source, sort_order)
            VALUES (?, 'Delete', '', 'Delete Generation', 'generation', 'generate',
                    'Delete the last generation of the current tab, after confirmation', 1, 'system', 19)
            """,
            (_ACTION_ID,),
        )
    print(
        f"Migration 094_add_delete_current_generation_keybinding: seeded "
        f"'{_ACTION_ID}' default keybinding"
    )
