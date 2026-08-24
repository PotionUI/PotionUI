"""Migration 134 renames the autocomplete_categories/autocomplete_values tables
(and the generation_segment_autocomplete link table + its autocomplete_value_id
column) to phrasebook_categories/phrasebook_values/generation_segment_phrasebook,
recreating their indexes/triggers under matching names, and renames the seeded
`go_autocomplete` keybinding id to `go_phrasebook` on both keybinding_defaults
and any user_keybindings override.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from src.platform.database.database import Database

_MIGRATIONS = (
    Path(__file__).resolve().parents[3]
    / "src" / "platform" / "database" / "migrations"
)


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration134(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

    def tearDown(self):
        Database._instance = None

    def _create_pre_134_schema(self):
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE autocomplete_categories (
                    id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    parent_id TEXT,
                    user_id TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE autocomplete_values (
                    id TEXT PRIMARY KEY,
                    category_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    sort_order INTEGER DEFAULT 0,
                    user_id TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    preview_file_id TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE generation_segment_autocomplete (
                    id TEXT PRIMARY KEY,
                    segment_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    autocomplete_value_id TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX idx_autocomplete_categories_path ON autocomplete_categories(path)"
            )
            conn.execute(
                "CREATE INDEX idx_autocomplete_values_category_id ON autocomplete_values(category_id)"
            )
            conn.execute(
                "CREATE INDEX idx_generation_segment_autocomplete_value "
                "ON generation_segment_autocomplete(autocomplete_value_id)"
            )
            conn.execute("""
                CREATE TRIGGER update_autocomplete_categories_updated_at
                AFTER UPDATE ON autocomplete_categories
                FOR EACH ROW
                BEGIN
                    SELECT 1;
                END
            """)
            conn.execute(
                "INSERT INTO autocomplete_categories (id, path, user_id) VALUES "
                "('cat-1', 'camera', 'user-1')"
            )
            conn.execute(
                "INSERT INTO autocomplete_values (id, category_id, label, user_id) VALUES "
                "('val-1', 'cat-1', 'Wide shot', 'user-1')"
            )
            conn.execute("""
                CREATE TABLE keybinding_defaults (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT
                )
            """)
            conn.execute(
                "INSERT INTO keybinding_defaults (id, key, label, description) VALUES "
                "('go_autocomplete', '4', 'Go to Autocomplete', 'Navigate to Autocomplete page')"
            )
            conn.execute("""
                CREATE TABLE user_keybindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    key TEXT
                )
            """)
            conn.execute(
                "INSERT INTO user_keybindings (user_id, action_id, key) VALUES "
                "('user-1', 'go_autocomplete', '9')"
            )
            conn.commit()

    def _tables(self, conn):
        return {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    def test_renames_tables_preserving_data(self):
        self._create_pre_134_schema()
        _load_migration("134_rename_autocomplete_to_phrasebook", self.db).up()

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
            self.assertIn("phrasebook_categories", tables)
            self.assertIn("phrasebook_values", tables)
            self.assertIn("generation_segment_phrasebook", tables)
            self.assertNotIn("autocomplete_categories", tables)
            self.assertNotIn("autocomplete_values", tables)
            self.assertNotIn("generation_segment_autocomplete", tables)

            category = conn.execute("SELECT * FROM phrasebook_categories WHERE id = 'cat-1'").fetchone()
            self.assertEqual(category["path"], "camera")
            value = conn.execute("SELECT * FROM phrasebook_values WHERE id = 'val-1'").fetchone()
            self.assertEqual(value["label"], "Wide shot")
            self.assertEqual(value["category_id"], "cat-1")

    def test_renames_link_column_and_indexes_and_triggers(self):
        self._create_pre_134_schema()
        _load_migration("134_rename_autocomplete_to_phrasebook", self.db).up()

        with self.db.get_connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(generation_segment_phrasebook)")}
            self.assertIn("phrasebook_value_id", columns)
            self.assertNotIn("autocomplete_value_id", columns)

            names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"
                )
            }
            self.assertIn("idx_phrasebook_categories_path", names)
            self.assertIn("idx_phrasebook_values_category_id", names)
            self.assertIn("idx_generation_segment_phrasebook_value", names)
            self.assertIn("update_phrasebook_categories_updated_at", names)
            self.assertNotIn("idx_autocomplete_categories_path", names)
            self.assertNotIn("update_autocomplete_categories_updated_at", names)

    def test_renames_keybinding_id_on_defaults_and_user_overrides(self):
        self._create_pre_134_schema()
        _load_migration("134_rename_autocomplete_to_phrasebook", self.db).up()

        with self.db.get_connection() as conn:
            default = conn.execute(
                "SELECT * FROM keybinding_defaults WHERE id = 'go_phrasebook'"
            ).fetchone()
            self.assertIsNotNone(default)
            self.assertEqual(default["label"], "Go to Phrasebook")
            self.assertIsNone(
                conn.execute("SELECT 1 FROM keybinding_defaults WHERE id = 'go_autocomplete'").fetchone()
            )

            override = conn.execute(
                "SELECT * FROM user_keybindings WHERE action_id = 'go_phrasebook'"
            ).fetchone()
            self.assertIsNotNone(override)
            self.assertEqual(override["key"], "9")

    def test_up_is_idempotent(self):
        self._create_pre_134_schema()
        migration = _load_migration("134_rename_autocomplete_to_phrasebook", self.db)
        migration.up()
        migration.up()  # must not raise

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
            self.assertIn("phrasebook_categories", tables)
            count = conn.execute("SELECT COUNT(*) AS c FROM phrasebook_categories").fetchone()["c"]
            self.assertEqual(count, 1)

    def test_down_reverts_tables_and_keybinding(self):
        self._create_pre_134_schema()
        migration = _load_migration("134_rename_autocomplete_to_phrasebook", self.db)
        migration.up()
        migration.down()

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
            self.assertIn("autocomplete_categories", tables)
            self.assertIn("autocomplete_values", tables)
            self.assertIn("generation_segment_autocomplete", tables)
            self.assertNotIn("phrasebook_categories", tables)

            category = conn.execute("SELECT * FROM autocomplete_categories WHERE id = 'cat-1'").fetchone()
            self.assertEqual(category["path"], "camera")

            default = conn.execute(
                "SELECT * FROM keybinding_defaults WHERE id = 'go_autocomplete'"
            ).fetchone()
            self.assertIsNotNone(default)
            self.assertEqual(default["label"], "Go to Autocomplete")

    def test_noop_when_tables_never_existed(self):
        migration = _load_migration("134_rename_autocomplete_to_phrasebook", self.db)
        migration.up()  # must not raise

        with self.db.get_connection() as conn:
            tables = self._tables(conn)
            self.assertNotIn("phrasebook_categories", tables)
            self.assertNotIn("autocomplete_categories", tables)


if __name__ == '__main__':
    unittest.main()
