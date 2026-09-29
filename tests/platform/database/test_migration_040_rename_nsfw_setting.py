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


class TestMigration040RenameNsfwSetting(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("040_rename_nsfw_setting", self.db)

    def tearDown(self):
        Database._instance = None

    def _row(self, key):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT id, value, value_type, type, description FROM settings WHERE key = ?",
                (key,),
            )
            return cursor.fetchone()

    def test_up_renames_row_keeping_value_type_and_scope(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE settings SET value = 'true' WHERE key = 'nsfw_filter'")
        self.migration.up()

        self.assertIsNone(self._row("nsfw_filter"))
        row = self._row("nsfw")
        self.assertEqual(row["value"], "true")
        self.assertEqual(row["value_type"], "boolean")
        self.assertEqual(row["type"], "USER")
        self.assertEqual(row["description"], "Allow NSFW content generation")

    def test_up_keeps_existing_user_overrides(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("SELECT id FROM settings WHERE key = 'nsfw_filter'")
            setting_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) VALUES ('u1', 'u1', 'u1@x', 'h')"
            )
            cursor.execute(
                "INSERT INTO user_settings (id, user_id, setting_id, value) VALUES ('us1', 'u1', ?, 'true')",
                (setting_id,),
            )
        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "SELECT us.value FROM user_settings us JOIN settings s ON s.id = us.setting_id "
                "WHERE s.key = 'nsfw' AND us.user_id = 'u1'"
            )
            self.assertEqual(cursor.fetchone()["value"], "true")

    def test_up_is_idempotent(self):
        self.migration.up()
        self.migration.up()

        self.assertIsNotNone(self._row("nsfw"))
        self.assertIsNone(self._row("nsfw_filter"))

    def test_up_drops_legacy_row_when_target_already_exists(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO settings (id, key, value, value_type, description, type) "
                "VALUES ('setting_nsfw', 'nsfw', 'true', 'boolean', 'x', 'USER')"
            )
        self.migration.up()

        self.assertIsNone(self._row("nsfw_filter"))
        self.assertEqual(self._row("nsfw")["value"], "true")

    def test_down_restores_legacy_key(self):
        self.migration.up()
        self.migration.down()

        self.assertIsNotNone(self._row("nsfw_filter"))
        self.assertIsNone(self._row("nsfw"))
