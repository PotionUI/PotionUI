"""Migration 119 adds `backends.driver` and backfills it from `engine`:
native rows become driver `native.local`; every other engine's rows keep
their engine name as the driver (the "engine-only registration" contract -
see the migration's docstring).
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


class TestMigration119(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True

        # backends starts as `type` (011), then `type` -> `engine` (069).
        _load_migration("011_create_backends", self.db).up()
        _load_migration("069_backends_type_to_engine", self.db).up()

    def tearDown(self):
        Database._instance = None

    def _insert_backend(self, backend_id, engine):
        with self.db.get_connection() as conn:
            conn.execute(
                "INSERT INTO backends (id, name, engine, enabled, is_default, config) "
                "VALUES (?, ?, ?, 1, 0, '{}')",
                (backend_id, backend_id, engine),
            )
            conn.commit()

    def _driver_of(self, backend_id):
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT driver FROM backends WHERE id = ?", (backend_id,)
            ).fetchone()
        return row["driver"] if row else None

    def test_adds_the_driver_column(self):
        _load_migration("119_add_backend_driver", self.db).up()

        with self.db.get_connection() as conn:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(backends)").fetchall()]
        self.assertIn("driver", columns)

    def test_native_row_backfills_to_native_local(self):
        self._insert_backend("native", "native")

        _load_migration("119_add_backend_driver", self.db).up()

        self.assertEqual(self._driver_of("native"), "native.local")

    def test_other_engine_row_backfills_to_its_own_engine_name(self):
        self._insert_backend("comfy-1", "comfyui")

        _load_migration("119_add_backend_driver", self.db).up()

        self.assertEqual(self._driver_of("comfy-1"), "comfyui")

    def test_driver_is_not_null_for_every_row(self):
        self._insert_backend("native", "native")
        self._insert_backend("comfy-1", "comfyui")

        _load_migration("119_add_backend_driver", self.db).up()

        with self.db.get_connection() as conn:
            rows = conn.execute("SELECT driver FROM backends").fetchall()
        self.assertTrue(all(r["driver"] for r in rows))

    def test_running_twice_is_a_no_op(self):
        self._insert_backend("native", "native")
        migration = _load_migration("119_add_backend_driver", self.db)

        migration.up()
        migration.up()

        self.assertEqual(self._driver_of("native"), "native.local")


if __name__ == '__main__':
    unittest.main()
