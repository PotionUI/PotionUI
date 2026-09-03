"""010 seeds the `toggle_floating_form` `keybinding_defaults` row for every
database - `001_baseline.py` predates this shortcut and never seeds it, so
this migration is what puts it there on a fresh install as well as an
upgrade.
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


class TestMigration010AddToggleFloatingFormKeybinding(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE applied_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_name TEXT UNIQUE NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("010_add_toggle_floating_form_keybinding", self.db)

    def tearDown(self):
        Database._instance = None

    def test_seeds_the_default_row(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT key, modifiers, label, category, context, enabled "
                "FROM keybinding_defaults WHERE id = 'toggle_floating_form'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 'q')
        self.assertEqual(row[1], '')
        self.assertEqual(row[4], 'generate')
        self.assertEqual(row[5], 1)

    def test_baseline_alone_has_no_such_row(self):
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM keybinding_defaults WHERE id = 'toggle_floating_form'"
            ).fetchone()
        self.assertIsNone(row)

    def test_idempotent(self):
        self.migration.up()
        self.migration.up()

        with self.db.get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM keybinding_defaults WHERE id = 'toggle_floating_form'"
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == '__main__':
    unittest.main()
