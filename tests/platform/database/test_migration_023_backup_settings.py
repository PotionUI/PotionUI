"""023 seeds the three backup settings - idempotently, since the migration
runner replays nothing but a second `up()` must still be harmless.
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

_KEYS = ("backup_destination", "backup_retention", "backup_default_tier")


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration023BackupSettings(unittest.TestCase):

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
        self.migration = _load_migration("023_backup_settings", self.db)

    def tearDown(self):
        Database._instance = None

    def _settings(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT key, value, value_type, type FROM settings WHERE key IN (?, ?, ?)", _KEYS
            )
            return {row["key"]: dict(row) for row in cursor.fetchall()}

    def test_the_three_settings_are_seeded_as_system_settings(self):
        self.assertEqual(self._settings(), {})

        self.migration.up()

        seeded = self._settings()
        self.assertEqual(set(seeded), set(_KEYS))
        self.assertEqual(seeded["backup_destination"]["value"], "backups")
        self.assertEqual(seeded["backup_retention"]["value"], "7")
        self.assertEqual(seeded["backup_default_tier"]["value"], "config")
        self.assertEqual({row["type"] for row in seeded.values()}, {"SYSTEM"})

    def test_a_second_run_leaves_an_edited_value_alone(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE settings SET value = '/srv/backups' WHERE key = 'backup_destination'"
            )

        self.migration.up()

        seeded = self._settings()
        self.assertEqual(seeded["backup_destination"]["value"], "/srv/backups")
        self.assertEqual(len(seeded), 3)

    def test_down_removes_them(self):
        self.migration.up()

        self.migration.down()

        self.assertEqual(self._settings(), {})


if __name__ == "__main__":
    unittest.main()
