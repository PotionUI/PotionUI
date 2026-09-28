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


class TestMigration036NativeAvailabilityLogicalRefs(unittest.TestCase):

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
        self.migration = _load_migration("036_native_availability_logical_refs", self.db)

    def tearDown(self):
        self.cwd_patch.stop()
        Database._instance = None

    def _cursor(self):
        return self.db.get_cursor()

    def _insert_backend(self, driver="native.local"):
        backend_id = generate_ulid()
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO backends (id, name, engine, driver) VALUES (?, 'Native', 'native', ?)",
                (backend_id, driver),
            )
        return backend_id

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

    def _insert_availability(self, model_id, backend_id, ref):
        availability_id = generate_ulid()
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO model_availability (id, model_id, backend_id, ref) VALUES (?, ?, ?, ?)",
                (availability_id, model_id, backend_id, ref),
            )
        return availability_id

    def _ref(self, availability_id):
        with self._cursor() as cursor:
            cursor.execute("SELECT ref FROM model_availability WHERE id = ?", (availability_id,))
            row = cursor.fetchone()
            return row["ref"] if row else None

    def test_native_local_ref_rewritten_to_logical(self):
        backend_id = self._insert_backend()
        model_id = self._insert_model("lora", "c.safetensors", "models/loras/c.safetensors")
        self._insert_location(model_id, "lora", "c.safetensors")
        availability_id = self._insert_availability(model_id, backend_id, "models/loras/c.safetensors")

        self.migration.up()

        self.assertEqual(self._ref(availability_id), "loras/c.safetensors")

    def test_windows_backslashes_in_the_old_ref_are_rewritten(self):
        backend_id = self._insert_backend()
        model_id = self._insert_model("lora", "d.safetensors", None)
        self._insert_location(model_id, "lora", "sdxl/d.safetensors")
        availability_id = self._insert_availability(model_id, backend_id, "models\\loras\\sdxl\\d.safetensors")

        self.migration.up()

        self.assertEqual(self._ref(availability_id), "loras/sdxl/d.safetensors")

    def test_unmappable_ref_is_dropped(self):
        backend_id = self._insert_backend()
        model_id = self._insert_model("lora", "e.safetensors", None)
        availability_id = self._insert_availability(model_id, backend_id, "/nowhere/near/home/e.safetensors")

        self.migration.up()

        with self._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM model_availability WHERE id = ?", (availability_id,))
            self.assertEqual(cursor.fetchone()["n"], 0)

    def test_remote_backend_refs_are_left_alone(self):
        backend_id = self._insert_backend(driver="comfyui")
        model_id = self._insert_model("checkpoint", "f.safetensors", None)
        availability_id = self._insert_availability(model_id, backend_id, "style/f.safetensors")

        self.migration.up()

        self.assertEqual(self._ref(availability_id), "style/f.safetensors")

    def test_second_run_is_a_no_op(self):
        backend_id = self._insert_backend()
        model_id = self._insert_model("lora", "g.safetensors", "models/loras/g.safetensors")
        self._insert_location(model_id, "lora", "g.safetensors")
        availability_id = self._insert_availability(model_id, backend_id, "models/loras/g.safetensors")

        self.migration.up()
        first = self._ref(availability_id)
        self.migration.up()
        second = self._ref(availability_id)

        self.assertEqual(first, "loras/g.safetensors")
        self.assertEqual(second, first)

    def test_winner_by_binding_position_is_used_when_two_locations_present(self):
        backend_id = self._insert_backend()
        model_id = self._insert_model("lora", "h.safetensors", "models/loras/h.safetensors")
        other_root_id = generate_ulid()
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO model_roots (id, label, path, path_key, kind, read_only, case_insensitive, state) "
                "VALUES (?, 'Library', '/lib', '/lib', 'library', 0, 0, 'online')",
                (other_root_id,),
            )
            cursor.execute(
                "INSERT INTO model_root_bindings (root_id, model_type, subdir, position, is_write) "
                "VALUES (?, 'lora', 'loras', 5, 0)",
                (other_root_id,),
            )
        self._insert_location(model_id, "lora", "from-library.safetensors", root_id=other_root_id)
        self._insert_location(model_id, "lora", "h.safetensors", root_id="home")
        availability_id = self._insert_availability(model_id, backend_id, "models/loras/h.safetensors")

        self.migration.up()

        self.assertEqual(self._ref(availability_id), "loras/h.safetensors")

    def test_down_restores_a_physical_form(self):
        backend_id = self._insert_backend()
        model_id = self._insert_model("lora", "i.safetensors", "models/loras/i.safetensors")
        self._insert_location(model_id, "lora", "i.safetensors")
        availability_id = self._insert_availability(model_id, backend_id, "models/loras/i.safetensors")
        self.migration.up()

        self.migration.down()

        self.assertEqual(self._ref(availability_id), "models/loras/i.safetensors")


if __name__ == "__main__":
    unittest.main()
