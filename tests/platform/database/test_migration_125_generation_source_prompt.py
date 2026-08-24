"""Migration 125 adds `generations.source_prompt_id` (nullable, no FK - a
deleted prompt must not break generation history) plus an index for the
per-prompt generations lookup and usage aggregate. See the migration's
docstring for the FK rationale.
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


class TestMigration125(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

        _load_migration("002_create_generations", self.db).up()

    def tearDown(self):
        Database._instance = None

    def _insert_generation(self, gen_id="g1"):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO generations (id, preset_name, form_data) VALUES (?, 'p', '{}')",
                (gen_id,),
            )
            conn.commit()

    def test_up_adds_the_column(self):
        _load_migration("125_add_generation_source_prompt", self.db).up()

        with self.db.get_connection() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(generations)")}
        self.assertIn("source_prompt_id", columns)

    def test_up_leaves_existing_rows_null(self):
        self._insert_generation()

        _load_migration("125_add_generation_source_prompt", self.db).up()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT source_prompt_id FROM generations WHERE id = 'g1'"
            ).fetchone()
        self.assertIsNone(row["source_prompt_id"])

    def test_up_creates_an_index(self):
        _load_migration("125_add_generation_source_prompt", self.db).up()

        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_generations_source_prompt_id'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_up_is_idempotent(self):
        migration = _load_migration("125_add_generation_source_prompt", self.db)
        migration.up()
        migration.up()  # must not raise "duplicate column"

    def test_down_drops_the_column_and_index(self):
        migration = _load_migration("125_add_generation_source_prompt", self.db)
        migration.up()

        migration.down()

        with self.db.get_connection() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(generations)")}
            index = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_generations_source_prompt_id'"
            ).fetchone()
        self.assertNotIn("source_prompt_id", columns)
        self.assertIsNone(index)

    def test_down_is_idempotent(self):
        migration = _load_migration("125_add_generation_source_prompt", self.db)
        migration.up()
        migration.down()
        migration.down()  # must not raise


if __name__ == '__main__':
    unittest.main()
