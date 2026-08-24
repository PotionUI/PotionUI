"""002 renames the `clip` model type to `text_encoder` and moves every stored
reference to the depot's `clip` directory onto `text_encoders`.

The interesting half of this migration is what it must NOT touch. `clip` is a
substring of plenty of things that have nothing to do with the text-encoder
bucket - a lora named `clip-fix.safetensors`, a `clip_vision` directory, the
`clip_skip` form key, a prompt that mentions a video clip - so every case
below pairs a row that must move with a near-miss that must stay exactly
where it is. A blind `REPLACE(col, 'clip', 'text_encoders')` passes none of
them.
"""

import importlib.util
import json
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


class TestMigration002RenameClipToTextEncoder(unittest.TestCase):

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
        self.migration = _load_migration("002_rename_clip_to_text_encoder", self.db)
        self._seed()

    def tearDown(self):
        Database._instance = None

    def _seed(self):
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE settings SET value = 'models' WHERE key = 'models_dir'")

            cursor.executemany(
                "INSERT INTO models (id, filename, file_path, model_type) VALUES (?, ?, ?, ?)",
                [
                    ("m_flat", "umt5.safetensors", "models/clip/umt5.safetensors", "clip"),
                    ("m_nested", "g3.safetensors", "models/clip/vendor/g3.safetensors", "clip"),
                    ("m_abs", "abs.safetensors", "/srv/depot/clip/abs.safetensors", "clip"),
                    ("m_lora", "clip-fix.safetensors", "models/loras/clip-fix.safetensors", "lora"),
                    ("m_vision", "cv.safetensors", "models/clip_vision/cv.safetensors", "unet"),
                    ("m_ckpt", "clip_l.safetensors", "models/checkpoints/clip_l.safetensors", "checkpoint"),
                ],
            )

            cursor.executemany(
                "INSERT INTO model_hash_cache (path, size, mtime_ns, sha256) VALUES (?, 1, 1, 'x')",
                [
                    ("models/clip/umt5.safetensors",),
                    ("models/loras/clip-fix.safetensors",),
                    ("models/clip_vision/cv.safetensors",),
                ],
            )

            cursor.executemany(
                "INSERT INTO downloads (id, url, destination_path, filename) VALUES (?, ?, ?, ?)",
                [
                    ("d_file", "http://x", "models/clip/new-te.safetensors", "new-te.safetensors"),
                    ("d_dir", "http://x", "models/clip", "repo"),
                    ("d_other", "http://x", "models/loras/clipart.safetensors", "clipart.safetensors"),
                ],
            )

            cursor.executemany(
                "INSERT INTO generations (id, form_data) VALUES (?, ?)",
                [
                    ("g_te", json.dumps({
                        "text_encoder": "models/clip/umt5.safetensors",
                        "loras": [{"model": "models/loras/clip-fix.safetensors"}],
                        "clip_skip": 2,
                        "prompt": "a clip of a dog",
                    })),
                    ("g_plain", json.dumps({"checkpoint": "models/checkpoints/clip_l.safetensors"})),
                ],
            )

            cursor.executemany(
                "INSERT INTO llm_configurations (id, name, type, base_url, model, system_message) "
                "VALUES (?, ?, ?, '', ?, '')",
                [
                    ("l_te", "adopted", "native", "clip/qwen3-te.safetensors"),
                    ("l_remote", "hosted", "openai", "gpt-clip"),
                ],
            )

            cursor.executemany(
                "INSERT INTO presets (id, preset_id, configuration) VALUES (?, ?, ?)",
                [
                    ("p_tagged", "SDXL/base", json.dumps({"clip_tags": ["te"], "vae_tags": ["v"]})),
                    ("p_plain", "Flux/dev", json.dumps({"vae_tags": ["v"]})),
                ],
            )

            cursor.execute(
                "INSERT INTO model_attribute_definitions (id, key, label, field_type, model_types) "
                "VALUES ('a_arch', 'arch', 'Arch', 'text', ?)",
                (json.dumps(["clip", "checkpoint"]),),
            )

    def _column(self, table, key_column, value_column):
        with self.db.get_connection() as conn:
            return {
                row[0]: row[1] for row in conn.execute(
                    f"SELECT {key_column}, {value_column} FROM {table}"
                ).fetchall()
            }

    def test_retypes_clip_models_and_leaves_every_other_type_alone(self):
        self.migration.up()

        types = self._column("models", "id", "model_type")
        self.assertEqual(types["m_flat"], "text_encoder")
        self.assertEqual(types["m_nested"], "text_encoder")
        self.assertEqual(types["m_abs"], "text_encoder")
        self.assertEqual(types["m_lora"], "lora")
        self.assertEqual(types["m_vision"], "unet")
        self.assertEqual(types["m_ckpt"], "checkpoint")

    def test_moves_model_paths_onto_the_new_depot_directory(self):
        self.migration.up()

        paths = self._column("models", "id", "file_path")
        self.assertEqual(paths["m_flat"], "models/text_encoders/umt5.safetensors")
        self.assertEqual(paths["m_nested"], "models/text_encoders/vendor/g3.safetensors")
        self.assertEqual(paths["m_abs"], "/srv/depot/text_encoders/abs.safetensors")

    def test_leaves_paths_that_only_contain_the_substring_clip(self):
        self.migration.up()

        paths = self._column("models", "id", "file_path")
        self.assertEqual(paths["m_lora"], "models/loras/clip-fix.safetensors")
        self.assertEqual(paths["m_vision"], "models/clip_vision/cv.safetensors")
        self.assertEqual(paths["m_ckpt"], "models/checkpoints/clip_l.safetensors")

    def test_moves_hash_cache_entries_under_the_depot_directory_only(self):
        self.migration.up()

        with self.db.get_connection() as conn:
            paths = {row[0] for row in conn.execute("SELECT path FROM model_hash_cache")}
        self.assertEqual(paths, {
            "models/text_encoders/umt5.safetensors",
            "models/loras/clip-fix.safetensors",
            "models/clip_vision/cv.safetensors",
        })

    def test_moves_download_destinations_including_the_bare_directory_form(self):
        self.migration.up()

        destinations = self._column("downloads", "id", "destination_path")
        self.assertEqual(destinations["d_file"], "models/text_encoders/new-te.safetensors")
        self.assertEqual(destinations["d_dir"], "models/text_encoders")
        self.assertEqual(destinations["d_other"], "models/loras/clipart.safetensors")

    def test_rewrites_only_the_model_paths_inside_generation_form_data(self):
        self.migration.up()

        form_data = json.loads(self._column("generations", "id", "form_data")["g_te"])
        self.assertEqual(form_data["text_encoder"], "models/text_encoders/umt5.safetensors")
        self.assertEqual(form_data["loras"][0]["model"], "models/loras/clip-fix.safetensors")
        self.assertEqual(form_data["clip_skip"], 2)
        self.assertEqual(form_data["prompt"], "a clip of a dog")

    def test_repoints_adopted_text_encoder_llm_configurations(self):
        self.migration.up()

        models = self._column("llm_configurations", "id", "model")
        self.assertEqual(models["l_te"], "text_encoders/qwen3-te.safetensors")
        self.assertEqual(models["l_remote"], "gpt-clip")

    def test_renames_the_preset_configuration_key(self):
        self.migration.up()

        configs = self._column("presets", "id", "configuration")
        self.assertEqual(
            json.loads(configs["p_tagged"]),
            {"text_encoder_tags": ["te"], "vae_tags": ["v"]},
        )
        self.assertEqual(json.loads(configs["p_plain"]), {"vae_tags": ["v"]})

    def test_renames_the_type_inside_model_attribute_definitions(self):
        self.migration.up()

        model_types = self._column("model_attribute_definitions", "id", "model_types")
        self.assertEqual(json.loads(model_types["a_arch"]), ["text_encoder", "checkpoint"])

    def test_leaves_remote_backend_refs_alone(self):
        """`model_availability.ref` names a file in a ComfyUI server's own
        model tree, which this rename does not reach."""
        with self.db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO backends (id, name, engine) VALUES ('b', 'comfy', 'comfyui')"
            )
            cursor.execute(
                "INSERT INTO model_availability (id, model_id, backend_id, ref) "
                "VALUES ('av', 'm_flat', 'b', 'clip/umt5.safetensors')"
            )

        self.migration.up()

        self.assertEqual(
            self._column("model_availability", "id", "ref")["av"],
            "clip/umt5.safetensors",
        )

    def test_is_idempotent(self):
        self.migration.up()
        first = self._snapshot()

        self.migration.up()

        self.assertEqual(self._snapshot(), first)

    def _snapshot(self):
        return {
            "models": self._column("models", "id", "model_type"),
            "paths": self._column("models", "id", "file_path"),
            "hashes": self._column("model_hash_cache", "path", "sha256"),
            "downloads": self._column("downloads", "id", "destination_path"),
            "generations": self._column("generations", "id", "form_data"),
            "llm": self._column("llm_configurations", "id", "model"),
            "presets": self._column("presets", "id", "configuration"),
            "attrs": self._column("model_attribute_definitions", "id", "model_types"),
        }


if __name__ == "__main__":
    unittest.main()
