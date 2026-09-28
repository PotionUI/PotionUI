import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.platform.database.database import Database
from src.platform.util.ids import generate_ulid

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


class TestMigration037DropModelFilePath(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_root = Path(tempfile.mkdtemp())
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()

        self.cwd_patch = patch("pathlib.Path.cwd", return_value=self.repo_root)
        self.cwd_patch.start()

        _load_migration("035_model_roots", self.db).up()
        _load_migration("036_native_availability_logical_refs", self.db).up()
        self.migration = _load_migration("037_drop_model_file_path", self.db)

    def tearDown(self):
        self.cwd_patch.stop()
        Database._instance = None

    def _cursor(self):
        return self.db.get_cursor()

    def _has_column(self, table, column) -> bool:
        with self._cursor() as cursor:
            cursor.execute(f"PRAGMA table_info({table})")
            return any(row["name"] == column for row in cursor.fetchall())

    def _insert_model(self, model_type, filename, file_path=None):
        model_id = generate_ulid()
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO models (id, filename, file_path, model_type) VALUES (?, ?, ?, ?)",
                (model_id, filename, file_path, model_type),
            )
        return model_id

    def _insert_location(self, model_id, model_type, rel_path, *, root_id="home", status="present"):
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO model_locations (id, model_id, root_id, model_type, rel_path, rel_key, status, seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (generate_ulid(), model_id, root_id, model_type, rel_path, rel_path, status),
            )

    def test_up_drops_the_column_and_its_index(self):
        self._insert_model("lora", "a.safetensors", "models/loras/a.safetensors")

        self.migration.up()

        self.assertFalse(self._has_column("models", "file_path"))
        with self._cursor() as cursor:
            cursor.execute("SELECT name FROM sqlite_master WHERE name = 'idx_models_file_path'")
            self.assertIsNone(cursor.fetchone())

    def test_up_leaves_models_usable_without_file_path(self):
        self.migration.up()

        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO models (id, filename, model_type) VALUES (?, ?, ?)",
                (generate_ulid(), "b.safetensors", "lora"),
            )

    def test_up_is_idempotent_on_a_second_run(self):
        self._insert_model("lora", "c.safetensors", "models/loras/c.safetensors")

        self.migration.up()
        self.migration.up()

        self.assertFalse(self._has_column("models", "file_path"))

    def test_down_restores_file_path_from_the_winning_location(self):
        model_id = self._insert_model("lora", "d.safetensors", "models/loras/d.safetensors")
        self._insert_location(model_id, "lora", "d.safetensors")
        self.migration.up()

        self.migration.down()

        with self._cursor() as cursor:
            cursor.execute("SELECT file_path FROM models WHERE id = ?", (model_id,))
            row = cursor.fetchone()
        self.assertIsNotNone(row["file_path"])
        self.assertTrue(row["file_path"].replace("\\", "/").endswith("d.safetensors"))

    def test_down_restores_windows_style_root_paths(self):
        model_id = self._insert_model("lora", "e.safetensors", "models/loras/e.safetensors")
        with self._cursor() as cursor:
            cursor.execute("UPDATE model_roots SET path = ? WHERE id = 'home'", ("D:\\ComfyUI\\models",))
        self._insert_location(model_id, "lora", "sdxl/e.safetensors")
        self.migration.up()

        self.migration.down()

        with self._cursor() as cursor:
            cursor.execute("SELECT file_path FROM models WHERE id = ?", (model_id,))
            row = cursor.fetchone()
        self.assertIn("e.safetensors", row["file_path"])

    def test_down_leaves_a_model_with_no_location_unset(self):
        model_id = self._insert_model("checkpoint", "f.safetensors", None)
        self.migration.up()

        self.migration.down()

        with self._cursor() as cursor:
            cursor.execute("SELECT file_path FROM models WHERE id = ?", (model_id,))
            row = cursor.fetchone()
        self.assertIsNone(row["file_path"])

    def test_down_is_safe_to_run_twice(self):
        model_id = self._insert_model("lora", "g.safetensors", "models/loras/g.safetensors")
        self._insert_location(model_id, "lora", "g.safetensors")
        self.migration.up()

        self.migration.down()
        self.migration.down()

        self.assertTrue(self._has_column("models", "file_path"))
        with self._cursor() as cursor:
            cursor.execute("SELECT file_path FROM models WHERE id = ?", (model_id,))
            row = cursor.fetchone()
        self.assertIsNotNone(row["file_path"])


if __name__ == "__main__":
    unittest.main()
