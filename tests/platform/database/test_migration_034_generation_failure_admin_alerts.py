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

_KEYS = ("notify_admins_on_generation_failure", "notify_admins_on_generation_failure_categories")


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration034GenerationFailureAdminAlerts(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("034_generation_failure_admin_alerts", self.db)

    def tearDown(self):
        Database._instance = None

    def _rows(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT key, value, value_type, type FROM settings WHERE key IN (?, ?) ORDER BY key", _KEYS
            )
            return [tuple(row) for row in cursor.fetchall()]

    def test_up_seeds_disabled_system_settings(self):
        self.migration.up()

        self.assertEqual(self._rows(), [
            ("notify_admins_on_generation_failure", "false", "boolean", "SYSTEM"),
            ("notify_admins_on_generation_failure_categories", "[]", "json", "SYSTEM"),
        ])

    def test_up_is_idempotent_and_keeps_existing_values(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE settings SET value = 'true' WHERE key = ?", (_KEYS[0],))
        self.migration.up()

        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "true")

    def test_down_removes_both_and_is_idempotent(self):
        self.migration.up()
        self.migration.down()
        self.migration.down()

        self.assertEqual(self._rows(), [])

    def test_up_after_down_reseeds(self):
        self.migration.up()
        self.migration.down()
        self.migration.up()

        self.assertEqual(len(self._rows()), 2)
