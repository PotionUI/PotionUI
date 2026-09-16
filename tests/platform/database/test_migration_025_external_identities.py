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

_KEYS = (
    "external_login_auto_create",
    "external_login_default_group",
    "external_login_link_by_email",
)


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration025ExternalIdentities(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        with self.db.get_connection() as conn:
            conn.execute(
                "CREATE TABLE applied_migrations ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "migration_name TEXT UNIQUE NOT NULL, "
                "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("025_external_identities", self.db)

    def tearDown(self):
        Database._instance = None

    def _settings(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT key, value, value_type, type FROM settings WHERE key IN (?, ?, ?)",
                _KEYS,
            )
            return {row["key"]: dict(row) for row in cursor.fetchall()}

    def _table_exists(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='external_identities'"
            )
            return cursor.fetchone() is not None

    def _columns(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(external_identities)")
            return {row["name"] for row in cursor.fetchall()}

    def test_the_table_is_created_with_its_columns(self):
        self.assertFalse(self._table_exists())

        self.migration.up()

        self.assertTrue(self._table_exists())
        self.assertEqual(
            self._columns(),
            {"id", "issuer", "subject", "user_id", "created_at", "last_login_at"},
        )

    def test_issuer_and_subject_are_unique_together(self):
        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash, account_type) "
                "VALUES ('u1', 'one', 'one@example.com', 'h', 'USER')"
            )
            cursor.execute(
                "INSERT INTO external_identities (id, issuer, subject, user_id, created_at) "
                "VALUES ('e1', 'iss', 'sub', 'u1', '2026-01-01T00:00:00+00:00')"
            )

        with self.assertRaises(Exception):
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO external_identities (id, issuer, subject, user_id, created_at) "
                    "VALUES ('e2', 'iss', 'sub', 'u1', '2026-01-01T00:00:00+00:00')"
                )

    def test_the_three_settings_are_seeded_as_system_settings(self):
        self.assertEqual(self._settings(), {})

        self.migration.up()

        seeded = self._settings()
        self.assertEqual(set(seeded), set(_KEYS))
        self.assertEqual(seeded["external_login_auto_create"]["value"], "false")
        self.assertEqual(seeded["external_login_link_by_email"]["value"], "false")
        self.assertEqual(seeded["external_login_default_group"]["value"], "")
        self.assertEqual({row["type"] for row in seeded.values()}, {"SYSTEM"})

    def test_a_second_run_is_harmless_and_leaves_edits_alone(self):
        self.migration.up()
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE settings SET value = 'true' WHERE key = 'external_login_auto_create'"
            )

        self.migration.up()

        seeded = self._settings()
        self.assertEqual(seeded["external_login_auto_create"]["value"], "true")
        self.assertEqual(len(seeded), 3)
        self.assertTrue(self._table_exists())

    def test_down_removes_the_table_and_the_settings(self):
        self.migration.up()

        self.migration.down()

        self.assertFalse(self._table_exists())
        self.assertEqual(self._settings(), {})


if __name__ == "__main__":
    unittest.main()
