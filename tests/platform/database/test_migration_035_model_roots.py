import hashlib
import importlib.util
import os
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


class TestMigration035ModelRoots(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_root = Path(tempfile.mkdtemp())
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(self.temp_dir) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("035_model_roots", self.db)

        self.cwd_patch = patch("pathlib.Path.cwd", return_value=self.repo_root)
        self.cwd_patch.start()

    def tearDown(self):
        self.cwd_patch.stop()
        Database._instance = None

    def _cursor(self):
        return self.db.get_cursor()

    def _insert_model(
        self,
        model_type,
        filename,
        file_path,
        *,
        file_size=1024,
        sha256=None,
        is_available=1,
        is_directory=0,
    ):
        model_id = generate_ulid()
        with self._cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO models
                    (id, filename, file_path, file_size, sha256, model_type, is_available, is_directory)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (model_id, filename, file_path, file_size, sha256, model_type, is_available, is_directory),
            )
        return model_id

    def _setting(self, key, value, value_type="string"):
        with self._cursor() as cursor:
            cursor.execute("SELECT id FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE settings SET value = ? WHERE id = ?", (value, row["id"]))
                return
            cursor.execute(
                "INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'test setting', 'SYSTEM', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (generate_ulid(), key, value, value_type),
            )

    def _location_for(self, model_id):
        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM model_locations WHERE model_id = ?", (model_id,))
            return cursor.fetchone()

    def _unplaced(self):
        import json
        with self._cursor() as cursor:
            cursor.execute("SELECT value FROM settings WHERE key = 'model_roots_unplaced'")
            row = cursor.fetchone()
            return json.loads(row["value"]) if row else []

    def test_up_seeds_home_root_and_bindings(self):
        from src.platform.filesystem.model_types import MODEL_TYPES

        self.migration.up()

        with self._cursor() as cursor:
            cursor.execute("SELECT * FROM model_roots WHERE id = 'home'")
            root = cursor.fetchone()
            self.assertIsNotNone(root)
            self.assertEqual(root["kind"], "home")
            self.assertEqual(root["read_only"], 0)

            cursor.execute("SELECT model_type, is_write FROM model_root_bindings WHERE root_id = 'home'")
            bindings = {r["model_type"]: r["is_write"] for r in cursor.fetchall()}
            self.assertEqual(set(bindings.keys()), set(MODEL_TYPES))
            self.assertTrue(all(is_write == 1 for is_write in bindings.values()))

    def test_relative_path_under_home(self):
        model_id = self._insert_model("lora", "a.safetensors", "models/loras/a.safetensors")

        self.migration.up()

        location = self._location_for(model_id)
        self.assertIsNotNone(location)
        self.assertEqual(location["root_id"], "home")
        self.assertEqual(location["model_type"], "lora")
        self.assertEqual(location["rel_path"], "a.safetensors")
        self.assertEqual(location["status"], "present")

    def test_dot_prefixed_models_dir_setting(self):
        self._setting("models_dir", "./models")
        model_id = self._insert_model("lora", "dot.safetensors", "models/loras/dot.safetensors")

        self.migration.up()

        location = self._location_for(model_id)
        self.assertIsNotNone(location)
        self.assertEqual(location["root_id"], "home")
        self.assertEqual(location["rel_path"], "dot.safetensors")
        self.assertEqual(self._unplaced(), [])

    def test_absolute_path_under_home(self):
        abs_path = str(self.repo_root / "models" / "checkpoints" / "b.safetensors")
        model_id = self._insert_model("checkpoint", "b.safetensors", abs_path)

        self.migration.up()

        location = self._location_for(model_id)
        self.assertIsNotNone(location)
        self.assertEqual(location["rel_path"], "b.safetensors")

    def test_legacy_external_target(self):
        self._setting("models_location_external_path", "/mnt/external/models")
        model_id = self._insert_model(
            "lora", "y.safetensors", "/mnt/external/models/loras/y.safetensors"
        )

        self.migration.up()

        location = self._location_for(model_id)
        self.assertIsNotNone(location)
        self.assertEqual(location["root_id"], "home")
        self.assertEqual(location["rel_path"], "y.safetensors")

    def test_live_symlink_target(self):
        target_dir = self.repo_root / "external_checkpoints"
        target_dir.mkdir(parents=True)
        (target_dir / "z.safetensors").write_bytes(b"x")
        (self.repo_root / "models").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "models" / "checkpoints").symlink_to(target_dir, target_is_directory=True)

        model_id = self._insert_model(
            "checkpoint", "z.safetensors", str(target_dir / "z.safetensors")
        )

        self.migration.up()

        location = self._location_for(model_id)
        self.assertIsNotNone(location)
        self.assertEqual(location["root_id"], "home")
        self.assertEqual(location["rel_path"], "z.safetensors")

    def test_live_symlink_target_windows_short_name_stored_path(self):
        target_dir = self.repo_root / "external_checkpoints"
        target_dir.mkdir(parents=True)
        (target_dir / "z.safetensors").write_bytes(b"x")
        (self.repo_root / "models").mkdir(parents=True, exist_ok=True)
        (self.repo_root / "models" / "checkpoints").symlink_to(target_dir, target_is_directory=True)

        short_name_dir = str(target_dir).replace("external_checkpoints", "EXTERN~1")
        model_id = self._insert_model(
            "checkpoint", "z.safetensors", f"{short_name_dir}/z.safetensors"
        )

        real_realpath = os.path.realpath

        def fake_realpath(path, *args, **kwargs):
            resolved = real_realpath(path, *args, **kwargs)
            return resolved.replace("EXTERN~1", "external_checkpoints")

        with patch.object(self.migration, "is_windows", return_value=True), \
                patch("os.path.realpath", side_effect=fake_realpath):
            self.migration.up()

        location = self._location_for(model_id)
        self.assertIsNotNone(location)
        self.assertEqual(location["root_id"], "home")
        self.assertEqual(location["rel_path"], "z.safetensors")

    def test_outside_everything_lands_in_unplaced(self):
        self._insert_model(
            "lora", "orphan.safetensors", "/completely/unrelated/dir/orphan.safetensors"
        )

        self.migration.up()

        unplaced = self._unplaced()
        self.assertEqual(len(unplaced), 1)
        self.assertEqual(unplaced[0]["dir"], "/completely/unrelated/dir")
        self.assertEqual(unplaced[0]["count"], 1)
        self.assertEqual(unplaced[0]["types"], ["lora"])

    def test_null_file_path_skipped(self):
        model_id = self._insert_model("checkpoint", "remote.safetensors", None)

        self.migration.up()

        self.assertIsNone(self._location_for(model_id))
        self.assertEqual(self._unplaced(), [])

    def test_hf_directory_row(self):
        model_id = self._insert_model(
            "llm", "SomeModel", "models/llm/SomeModel", file_size=None, is_directory=1
        )

        self.migration.up()

        location = self._location_for(model_id)
        self.assertIsNotNone(location)
        self.assertEqual(location["rel_path"], "SomeModel")

    def test_is_available_false_gives_missing_status(self):
        model_id = self._insert_model(
            "vae", "broken.safetensors", "models/vae/broken.safetensors", is_available=0
        )

        self.migration.up()

        location = self._location_for(model_id)
        self.assertEqual(location["status"], "missing")

    def test_windows_backslashes(self):
        model_id = self._insert_model(
            "lora", "win.safetensors", "models\\loras\\win.safetensors"
        )

        self.migration.up()

        location = self._location_for(model_id)
        self.assertIsNotNone(location)
        self.assertEqual(location["rel_path"], "win.safetensors")

    def test_never_hashes(self):
        self._insert_model("lora", "a.safetensors", "models/loras/a.safetensors")

        with patch.object(hashlib, "sha256", side_effect=AssertionError("must not hash")):
            self.migration.up()

        with self._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM model_locations")
            self.assertEqual(cursor.fetchone()["n"], 1)

    def test_second_run_is_a_noop(self):
        self._insert_model("lora", "a.safetensors", "models/loras/a.safetensors")
        self._insert_model("lora", "orphan.safetensors", "/completely/unrelated/dir/orphan.safetensors")

        self.migration.up()
        with self._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM model_locations")
            locations_after_first = cursor.fetchone()["n"]
            cursor.execute("SELECT COUNT(*) AS n FROM model_roots")
            roots_after_first = cursor.fetchone()["n"]
            cursor.execute("SELECT COUNT(*) AS n FROM model_root_bindings")
            bindings_after_first = cursor.fetchone()["n"]
        unplaced_after_first = self._unplaced()

        self.migration.up()

        with self._cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS n FROM model_locations")
            self.assertEqual(cursor.fetchone()["n"], locations_after_first)
            cursor.execute("SELECT COUNT(*) AS n FROM model_roots")
            self.assertEqual(cursor.fetchone()["n"], roots_after_first)
            cursor.execute("SELECT COUNT(*) AS n FROM model_root_bindings")
            self.assertEqual(cursor.fetchone()["n"], bindings_after_first)
        self.assertEqual(self._unplaced(), unplaced_after_first)


if __name__ == "__main__":
    unittest.main()
