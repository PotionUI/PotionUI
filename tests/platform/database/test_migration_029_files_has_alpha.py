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


class TestMigration029FilesHasAlpha(unittest.TestCase):

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
        self.migration = _load_migration("029_files_has_alpha", self.db)

    def tearDown(self):
        Database._instance = None

    def _columns(self, table):
        with self.db.get_cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cursor.fetchall()}

    def test_has_alpha_column_lands_on_files(self):
        self.assertNotIn("has_alpha", self._columns("files"))

        self.migration.up()

        self.assertIn("has_alpha", self._columns("files"))

    def test_existing_rows_read_back_as_not_alpha(self):
        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES ('user1', 'tester', 'tester@example.com', 'hashed')"
            )
            cursor.execute(
                "INSERT INTO files (id, file_path, file_type, user_id) "
                "VALUES ('f1', 'a.png', 'IMAGE', 'user1')"
            )
            cursor.execute("SELECT has_alpha FROM files WHERE id = 'f1'")
            self.assertEqual(cursor.fetchone()["has_alpha"], 0)

    def test_a_second_run_changes_nothing(self):
        self.migration.up()
        before = self._columns("files")

        self.migration.up()

        self.assertEqual(before, self._columns("files"))


if __name__ == "__main__":
    unittest.main()
