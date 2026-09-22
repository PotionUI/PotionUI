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


class TestMigration028PromptVariables(unittest.TestCase):

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
        self.migration = _load_migration("028_prompt_variables", self.db)

        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, email, password_hash) "
                "VALUES ('u1', 'u1', 'u1@test.com', 'x')"
            )
            cursor.execute(
                "INSERT INTO prompts (id, user_id, flattened_text) VALUES ('p1', 'u1', 'a fox')"
            )

    def tearDown(self):
        Database._instance = None

    def _columns(self):
        with self.db.get_connection() as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(prompts)").fetchall()}

    def test_adds_the_variables_column(self):
        self.assertNotIn("variables", self._columns())

        self.migration.up()

        self.assertIn("variables", self._columns())

    def test_existing_rows_get_a_null_variables_value(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            row = conn.execute("SELECT variables FROM prompts WHERE id = 'p1'").fetchone()
        self.assertIsNone(row["variables"])

    def test_idempotent(self):
        self.migration.up()
        self.migration.up()

        self.assertIn("variables", self._columns())


if __name__ == '__main__':
    unittest.main()
