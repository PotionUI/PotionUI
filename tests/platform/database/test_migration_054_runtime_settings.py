import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from src.platform.database.database import Database

_MIGRATIONS = Path(__file__).resolve().parents[3] / "src" / "platform" / "database" / "migrations"


def _load_migration(stem, database):
    spec = importlib.util.spec_from_file_location(stem, _MIGRATIONS / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[stem] = module
    spec.loader.exec_module(module)
    module.db = database
    return module


class TestMigration054RuntimeSettings(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        Database._instance = None
        self.db = Database()
        self.db.db_path = Path(temp.name) / "test.sqlite"
        self.db._initialized = True
        _load_migration("001_baseline", self.db).up()
        self.migration = _load_migration("054_runtime_settings", self.db)

    def tearDown(self):
        Database._instance = None

    def _execute(self, sql, params=()):
        with self.db.get_cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def _settings(self):
        return {row["key"]: row for row in self._execute("SELECT key, value, value_type, type FROM settings")}

    def _add_backend(self, backend_id, driver, config=None, engine="native"):
        self._execute(
            "INSERT INTO backends (id, name, engine, driver, enabled, is_default, config) VALUES (?, ?, ?, ?, 1, 0, ?)",
            (backend_id, backend_id, engine, driver, json.dumps(config or {})),
        )

    def _flags(self, backend_id):
        row = self._execute("SELECT config FROM backends WHERE id = ?", (backend_id,))[0]
        return json.loads(row["config"]).get("engine_flags")

    def test_profiling_settings_are_seeded_with_defaults(self):
        self.migration.up(environ={})

        settings = self._settings()
        self.assertEqual(settings["profiling_enabled"]["value"], "false")
        self.assertEqual(settings["profiling_enabled"]["value_type"], "boolean")
        self.assertEqual(settings["profiling_enabled"]["type"], "SYSTEM")
        self.assertEqual(settings["profiling_census"]["value"], "true")

    def test_potionui_profile_seeds_the_profiling_setting_once(self):
        self.migration.up(environ={"POTIONUI_PROFILE": "1"})
        self.assertEqual(self._settings()["profiling_enabled"]["value"], "true")

        self._execute("UPDATE settings SET value = 'false' WHERE key = 'profiling_enabled'")
        self.migration.up(environ={"POTIONUI_PROFILE": "1"})

        self.assertEqual(self._settings()["profiling_enabled"]["value"], "false")

    def test_every_existing_native_backend_is_seeded_from_the_env_vars(self):
        self._add_backend("native", "native.local")
        self._add_backend("worker-a", "native.remote", {"base_url": "http://w"})
        self._add_backend("comfy", "comfyui", engine="comfyui")

        self.migration.up(environ={
            "NATIVE_FP8_MATMUL": "auto",
            "NATIVE_LORA_FUSED": "off",
            "NATIVE_FP8_QUANTIZE": "FORCE",
            "NATIVE_MIN_INFERENCE_MEMORY_GB": "3.5",
            "NATIVE_LTX_DIFFUSION_TILE_PX": "512",
            "NATIVE_SOL_ATTN_BACKEND": "kernel",
            "NATIVE_ATTENTION": "Sage2",
        })

        expected = {
            "native_fp8_matmul": True,
            "native_lora_fused": False,
            "native_fp8_quantize": "force",
            "native_min_inference_memory_gb": 3.5,
            "native_ltx_decode_tile_px": 512,
            "native_sol_attn_backend": "kernel",
            "native_attention_backend": "sage2",
        }
        self.assertEqual(self._flags("native"), expected)
        self.assertEqual(self._flags("worker-a"), expected)
        worker_config = json.loads(self._execute("SELECT config FROM backends WHERE id = 'worker-a'")[0]["config"])
        self.assertEqual(worker_config["base_url"], "http://w")
        self.assertIsNone(self._flags("comfy"))

    def test_without_env_vars_backends_get_an_empty_map_and_use_the_defaults(self):
        self._add_backend("native", "native.local")

        self.migration.up(environ={})

        self.assertEqual(self._flags("native"), {})

    def test_the_old_global_admin_choices_move_into_the_local_backend_only(self):
        self._execute("UPDATE settings SET value = 'on' WHERE key = 'native_torch_compile'")
        self._execute("UPDATE settings SET value = 'sdpa' WHERE key = 'native_attention_backend'")
        self._add_backend("native", "native.local")
        self._add_backend("worker-a", "native.remote")

        self.migration.up(environ={"NATIVE_ATTENTION": "flash", "NATIVE_STREAM_PREFETCH": "on"})

        self.assertEqual(self._flags("native"), {
            "native_torch_compile": True,
            "native_attention_backend": "sdpa",
            "native_stream_prefetch": True,
        })
        self.assertEqual(self._flags("worker-a"), {
            "native_attention_backend": "flash",
            "native_stream_prefetch": True,
        })
        settings = self._settings()
        for key in ("native_torch_compile", "native_stream_prefetch", "native_attention_backend"):
            self.assertNotIn(key, settings)

    def test_unusable_env_values_are_skipped(self):
        self._add_backend("native", "native.local")

        self.migration.up(environ={
            "NATIVE_FP8_MATMUL": "sometimes",
            "NATIVE_MIN_INFERENCE_MEMORY_GB": "lots",
            "NATIVE_FP8_QUANTIZE": "maybe",
            "NATIVE_NVFP4_MATMUL": "on",
        })

        self.assertEqual(self._flags("native"), {"native_nvfp4_matmul": True})

    def test_a_backend_that_already_has_engine_flags_is_not_reseeded(self):
        self._add_backend("native", "native.local", {"engine_flags": {"native_fp8_matmul": False}})

        self.migration.up(environ={"NATIVE_FP8_MATMUL": "on", "NATIVE_NVFP4_MATMUL": "on"})

        self.assertEqual(self._flags("native"), {"native_fp8_matmul": False})

    def test_down_restores_the_retired_settings_and_drops_engine_flags(self):
        self._add_backend("native", "native.local")
        self.migration.up(environ={"NATIVE_FP8_MATMUL": "on"})

        self.migration.down()

        settings = self._settings()
        self.assertNotIn("profiling_enabled", settings)
        self.assertEqual(settings["native_torch_compile"]["value"], "")
        self.assertIsNone(self._flags("native"))


if __name__ == "__main__":
    unittest.main()
