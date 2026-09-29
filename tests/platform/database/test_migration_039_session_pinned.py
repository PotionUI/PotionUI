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


class TestMigration039SessionPinned(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("039_session_pinned", self.db)
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
            cursor.execute(
                "INSERT INTO sessions (id, user_id, preset_id, name, data) VALUES ('old', 'u', 'p', 'Old', '{}')"
            )

    def tearDown(self):
        Database._instance = None

    def _columns(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(sessions)")
            return {row["name"] for row in cursor.fetchall()}

    def test_existing_sessions_come_through_unpinned(self):
        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT pinned, pinned_at FROM sessions WHERE id = 'old'")
            row = cursor.fetchone()
        self.assertEqual(row["pinned"], 0)
        self.assertIsNone(row["pinned_at"])

    def test_up_is_idempotent(self):
        self.migration.up()
        self.migration.up()

        self.assertLessEqual({"pinned", "pinned_at"}, self._columns())

    def test_down_drops_both_columns(self):
        self.migration.up()

        self.migration.down()

        self.assertFalse({"pinned", "pinned_at"} & self._columns())
