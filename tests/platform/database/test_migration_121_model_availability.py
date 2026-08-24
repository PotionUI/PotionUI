"""Migration 121 adds `models.is_available` (default 1) and `models.unavailable_at`
so the indexer can soft-mark a model whose file went missing instead of deleting
its row - see src.features.models.location and the migration's docstring.
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


class TestMigration121(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

        _load_migration("006_create_models", self.db).up()

    def tearDown(self):
        Database._instance = None

    def _insert_model(self, model_id="m1"):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO models (id, filename, file_path, model_type) "
                "VALUES (?, 'x.safetensors', '/models/checkpoints/x.safetensors', 'checkpoint')",
                (model_id,),
            )
            conn.commit()

    def _row(self, model_id="m1"):
        with self.db.get_connection() as conn:
            return conn.execute(
                "SELECT is_available, unavailable_at FROM models WHERE id = ?", (model_id,)
            ).fetchone()

    def test_up_adds_columns_and_defaults_existing_rows_to_available(self):
        self._insert_model()

        _load_migration("121_add_model_availability", self.db).up()

        row = self._row()
        self.assertEqual(row["is_available"], 1)
        self.assertIsNone(row["unavailable_at"])

    def test_up_is_idempotent(self):
        migration = _load_migration("121_add_model_availability", self.db)
        migration.up()
        migration.up()  # must not raise "duplicate column"

    def test_down_drops_the_columns(self):
        migration = _load_migration("121_add_model_availability", self.db)
        migration.up()

        migration.down()

        with self.db.get_connection() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
        self.assertNotIn("is_available", columns)
        self.assertNotIn("unavailable_at", columns)

    def test_down_is_idempotent(self):
        migration = _load_migration("121_add_model_availability", self.db)
        migration.up()
        migration.down()
        migration.down()  # must not raise
