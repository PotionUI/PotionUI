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

_COLUMNS = {
    "error_code",
    "error_user_message",
    "error_detail",
    "failed_pipe_id",
    "failed_pipe_name",
    "failed_at_step",
}


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration031GenerationFailureDetail(unittest.TestCase):

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
        self.migration = _load_migration("031_generation_failure_detail", self.db)

    def tearDown(self):
        Database._instance = None

    def _columns(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(generations)")
            return {row[1] for row in cursor.fetchall()}

    def test_failure_columns_land_on_generations(self):
        self.assertFalse(_COLUMNS & self._columns())

        self.migration.up()

        self.assertTrue(_COLUMNS <= self._columns())

    def test_existing_rows_read_back_with_no_failure_detail(self):
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO generations (id, form_data, status, error_message) "
                "VALUES ('g1', '{}', 'failed', 'old failure')"
            )

        self.migration.up()

        with self.db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM generations WHERE id = 'g1'")
            row = cursor.fetchone()
        self.assertEqual(row["error_message"], "old failure")
        for column in _COLUMNS:
            self.assertIsNone(row[column])

    def test_a_second_run_changes_nothing(self):
        self.migration.up()
        before = self._columns()

        self.migration.up()

        self.assertEqual(before, self._columns())


if __name__ == "__main__":
    unittest.main()
