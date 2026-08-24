"""Migration 134: Rename the autocomplete domain to phrasebook.

The `#category.path` chip syntax is unchanged; only the feature's own name
changes (backend package, API routes, MCP/LLM tool names, frontend route).
Renames the three tables that back it and their indexes/triggers so the
schema matches the new code:

  - autocomplete_categories -> phrasebook_categories
  - autocomplete_values -> phrasebook_values
  - generation_segment_autocomplete -> generation_segment_phrasebook
    (column autocomplete_value_id -> phrasebook_value_id)

Also renames the seeded 'go_autocomplete' keybinding id (and any user
override referencing it) to 'go_phrasebook'.
"""

from src.platform.database.database import db


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def up():
    """Rename autocomplete_* tables/indexes/triggers to phrasebook_*."""
    with db.get_cursor() as cursor:
        if _table_exists(cursor, "autocomplete_categories") and not _table_exists(cursor, "phrasebook_categories"):
            cursor.execute("ALTER TABLE autocomplete_categories RENAME TO phrasebook_categories")

            cursor.execute("DROP TRIGGER IF EXISTS update_autocomplete_categories_updated_at")
            cursor.execute("""
                CREATE TRIGGER update_phrasebook_categories_updated_at
                AFTER UPDATE ON phrasebook_categories
                FOR EACH ROW
                BEGIN
                    UPDATE phrasebook_categories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                END
            """)

            cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_categories_path")
            cursor.execute("CREATE INDEX idx_phrasebook_categories_path ON phrasebook_categories(path)")
            cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_categories_user_id")
            cursor.execute("CREATE INDEX idx_phrasebook_categories_user_id ON phrasebook_categories(user_id)")
            cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_categories_parent_id")
            cursor.execute("CREATE INDEX idx_phrasebook_categories_parent_id ON phrasebook_categories(parent_id)")
            cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_categories_is_active")
            cursor.execute("CREATE INDEX idx_phrasebook_categories_is_active ON phrasebook_categories(is_active)")

        if _table_exists(cursor, "autocomplete_values") and not _table_exists(cursor, "phrasebook_values"):
            cursor.execute("ALTER TABLE autocomplete_values RENAME TO phrasebook_values")

            cursor.execute("DROP TRIGGER IF EXISTS update_autocomplete_values_updated_at")
            cursor.execute("""
                CREATE TRIGGER update_phrasebook_values_updated_at
                AFTER UPDATE ON phrasebook_values
                FOR EACH ROW
                BEGIN
                    UPDATE phrasebook_values SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                END
            """)

            cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_values_category_id")
            cursor.execute("CREATE INDEX idx_phrasebook_values_category_id ON phrasebook_values(category_id)")
            cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_values_user_id")
            cursor.execute("CREATE INDEX idx_phrasebook_values_user_id ON phrasebook_values(user_id)")
            cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_values_sort_order")
            cursor.execute("CREATE INDEX idx_phrasebook_values_sort_order ON phrasebook_values(sort_order)")
            cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_values_is_active")
            cursor.execute("CREATE INDEX idx_phrasebook_values_is_active ON phrasebook_values(is_active)")
            cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_values_preview_file_id")
            cursor.execute(
                "CREATE INDEX idx_phrasebook_values_preview_file_id ON phrasebook_values(preview_file_id)"
            )

        if _table_exists(cursor, "generation_segment_autocomplete") and not _table_exists(
            cursor, "generation_segment_phrasebook"
        ):
            cursor.execute(
                "ALTER TABLE generation_segment_autocomplete RENAME TO generation_segment_phrasebook"
            )
            cursor.execute(
                "ALTER TABLE generation_segment_phrasebook RENAME COLUMN autocomplete_value_id TO phrasebook_value_id"
            )

            cursor.execute("DROP INDEX IF EXISTS idx_generation_segment_autocomplete_segment")
            cursor.execute(
                "CREATE INDEX idx_generation_segment_phrasebook_segment "
                "ON generation_segment_phrasebook(segment_id)"
            )
            cursor.execute("DROP INDEX IF EXISTS idx_generation_segment_autocomplete_generation")
            cursor.execute(
                "CREATE INDEX idx_generation_segment_phrasebook_generation "
                "ON generation_segment_phrasebook(generation_id)"
            )
            cursor.execute("DROP INDEX IF EXISTS idx_generation_segment_autocomplete_value")
            cursor.execute(
                "CREATE INDEX idx_generation_segment_phrasebook_value "
                "ON generation_segment_phrasebook(phrasebook_value_id)"
            )

        # Stored keybinding id, referenced by keybinding_defaults.id and any
        # per-user override in user_keybindings.action_id (no ON UPDATE
        # CASCADE on that foreign key, so both need the same UPDATE).
        if _table_exists(cursor, "keybinding_defaults"):
            cursor.execute(
                "UPDATE keybinding_defaults SET id = 'go_phrasebook', "
                "label = 'Go to Phrasebook', description = 'Navigate to Phrasebook page' "
                "WHERE id = 'go_autocomplete'"
            )
        if _table_exists(cursor, "user_keybindings"):
            cursor.execute(
                "UPDATE user_keybindings SET action_id = 'go_phrasebook' WHERE action_id = 'go_autocomplete'"
            )

        print("Migration 134: Renamed autocomplete tables/indexes/triggers and keybinding id to phrasebook")


def down():
    """Revert phrasebook_* tables/indexes/triggers back to autocomplete_*."""
    with db.get_cursor() as cursor:
        if _table_exists(cursor, "phrasebook_categories") and not _table_exists(cursor, "autocomplete_categories"):
            cursor.execute("ALTER TABLE phrasebook_categories RENAME TO autocomplete_categories")

            cursor.execute("DROP TRIGGER IF EXISTS update_phrasebook_categories_updated_at")
            cursor.execute("""
                CREATE TRIGGER update_autocomplete_categories_updated_at
                AFTER UPDATE ON autocomplete_categories
                FOR EACH ROW
                BEGIN
                    UPDATE autocomplete_categories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                END
            """)

            cursor.execute("DROP INDEX IF EXISTS idx_phrasebook_categories_path")
            cursor.execute("CREATE INDEX idx_autocomplete_categories_path ON autocomplete_categories(path)")
            cursor.execute("DROP INDEX IF EXISTS idx_phrasebook_categories_user_id")
            cursor.execute("CREATE INDEX idx_autocomplete_categories_user_id ON autocomplete_categories(user_id)")
            cursor.execute("DROP INDEX IF EXISTS idx_phrasebook_categories_parent_id")
            cursor.execute(
                "CREATE INDEX idx_autocomplete_categories_parent_id ON autocomplete_categories(parent_id)"
            )
            cursor.execute("DROP INDEX IF EXISTS idx_phrasebook_categories_is_active")
            cursor.execute(
                "CREATE INDEX idx_autocomplete_categories_is_active ON autocomplete_categories(is_active)"
            )

        if _table_exists(cursor, "phrasebook_values") and not _table_exists(cursor, "autocomplete_values"):
            cursor.execute("ALTER TABLE phrasebook_values RENAME TO autocomplete_values")

            cursor.execute("DROP TRIGGER IF EXISTS update_phrasebook_values_updated_at")
            cursor.execute("""
                CREATE TRIGGER update_autocomplete_values_updated_at
                AFTER UPDATE ON autocomplete_values
                FOR EACH ROW
                BEGIN
                    UPDATE autocomplete_values SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                END
            """)

            cursor.execute("DROP INDEX IF EXISTS idx_phrasebook_values_category_id")
            cursor.execute("CREATE INDEX idx_autocomplete_values_category_id ON autocomplete_values(category_id)")
            cursor.execute("DROP INDEX IF EXISTS idx_phrasebook_values_user_id")
            cursor.execute("CREATE INDEX idx_autocomplete_values_user_id ON autocomplete_values(user_id)")
            cursor.execute("DROP INDEX IF EXISTS idx_phrasebook_values_sort_order")
            cursor.execute("CREATE INDEX idx_autocomplete_values_sort_order ON autocomplete_values(sort_order)")
            cursor.execute("DROP INDEX IF EXISTS idx_phrasebook_values_is_active")
            cursor.execute("CREATE INDEX idx_autocomplete_values_is_active ON autocomplete_values(is_active)")
            cursor.execute("DROP INDEX IF EXISTS idx_phrasebook_values_preview_file_id")
            cursor.execute(
                "CREATE INDEX idx_autocomplete_values_preview_file_id ON autocomplete_values(preview_file_id)"
            )

        if _table_exists(cursor, "generation_segment_phrasebook") and not _table_exists(
            cursor, "generation_segment_autocomplete"
        ):
            cursor.execute(
                "ALTER TABLE generation_segment_phrasebook RENAME COLUMN phrasebook_value_id TO autocomplete_value_id"
            )
            cursor.execute(
                "ALTER TABLE generation_segment_phrasebook RENAME TO generation_segment_autocomplete"
            )

            cursor.execute("DROP INDEX IF EXISTS idx_generation_segment_phrasebook_segment")
            cursor.execute(
                "CREATE INDEX idx_generation_segment_autocomplete_segment "
                "ON generation_segment_autocomplete(segment_id)"
            )
            cursor.execute("DROP INDEX IF EXISTS idx_generation_segment_phrasebook_generation")
            cursor.execute(
                "CREATE INDEX idx_generation_segment_autocomplete_generation "
                "ON generation_segment_autocomplete(generation_id)"
            )
            cursor.execute("DROP INDEX IF EXISTS idx_generation_segment_phrasebook_value")
            cursor.execute(
                "CREATE INDEX idx_generation_segment_autocomplete_value "
                "ON generation_segment_autocomplete(autocomplete_value_id)"
            )

        if _table_exists(cursor, "keybinding_defaults"):
            cursor.execute(
                "UPDATE keybinding_defaults SET id = 'go_autocomplete', "
                "label = 'Go to Autocomplete', description = 'Navigate to Autocomplete page' "
                "WHERE id = 'go_phrasebook'"
            )
        if _table_exists(cursor, "user_keybindings"):
            cursor.execute(
                "UPDATE user_keybindings SET action_id = 'go_autocomplete' WHERE action_id = 'go_phrasebook'"
            )

        print("Migration 134: Reverted phrasebook tables/indexes/triggers and keybinding id to autocomplete")
