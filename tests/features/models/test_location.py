"""Symlink-farm relocation and its guardrails.

Runs against a real migrated temp-file SQLite (for the settings round-trip)
and a real `tmp_path` directory tree for the symlinks - never the checked-out
`models/` directory, which on the dev box symlinks into production storage.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from tests.fixtures.persistence_base import PersistenceTestBase
from src.platform.settings.repository import SettingRepository
from src.features.models.location import (
    ModelsLocationError,
    ModelsRelocator,
    EXTERNAL_PATH_SETTING_KEY,
    OVERRIDES_SETTING_KEY,
)


@pytest.mark.skipif(
    os.name == "nt",
    reason="ModelsRelocator refuses to run on Windows; see test_apply_refuses_when_windows",
)
class TestModelsRelocator(PersistenceTestBase):
    def setUp(self):
        super().setUp()

        # A directory of our own, separate from PersistenceTestBase's temp_dir
        # (which its tearDown expects to still be empty except for the sqlite
        # file) - the symlink farm this test builds lives here instead.
        self.fs_tmp = tempfile.mkdtemp()
        self.tmp = Path(self.fs_tmp)
        self.models_root = self.tmp / "models"
        self.external = self.tmp / "external"
        self.manager = ModelsRelocator(self.models_root, SettingRepository())

    def tearDown(self):
        shutil.rmtree(self.fs_tmp, ignore_errors=True)
        super().tearDown()

    # ---- get_config ----

    def test_get_config_with_nothing_applied_yet(self):
        config = self.manager.get_config()

        self.assertIsNone(config["external_path"])
        self.assertEqual(config["overrides"], {})
        self.assertTrue(len(config["directories"]) > 0)
        self.assertTrue(all(not d["linked"] for d in config["directories"]))

    # ---- apply: happy path ----

    def test_apply_symlinks_every_type_directory_at_the_external_root(self):
        self.manager.apply(str(self.external))

        checkpoints_link = self.models_root / "checkpoints"
        self.assertTrue(checkpoints_link.is_symlink())
        self.assertEqual(checkpoints_link.resolve(), (self.external / "checkpoints").resolve())

    def test_apply_persists_the_external_path_setting(self):
        self.manager.apply(str(self.external))

        setting = self.manager.settings.get_setting_by_key(EXTERNAL_PATH_SETTING_KEY)
        self.assertEqual(setting.get_typed_value(), str(self.external))

    def test_apply_is_reflected_in_get_config(self):
        self.manager.apply(str(self.external))

        config = self.manager.get_config()

        self.assertEqual(config["external_path"], str(self.external))
        checkpoints = next(d for d in config["directories"] if d["directory"] == "checkpoints")
        self.assertTrue(checkpoints["linked"])

    def test_apply_a_file_written_through_the_symlink_is_visible_at_the_stable_root(self):
        """The whole point: the app keeps reading models_root/<type>/... unchanged."""
        self.manager.apply(str(self.external))

        (self.external / "loras").mkdir(parents=True, exist_ok=True)
        (self.external / "loras" / "x.safetensors").write_bytes(b"weights")

        self.assertTrue((self.models_root / "loras" / "x.safetensors").exists())

    def test_apply_is_idempotent_and_re_pointable(self):
        """Applying a second, different location must retarget the symlinks, not
        refuse because a symlink is already there."""
        first = self.tmp / "first"
        second = self.tmp / "second"

        self.manager.apply(str(first))
        self.manager.apply(str(second))

        link = self.models_root / "checkpoints"
        self.assertEqual(link.resolve(), (second / "checkpoints").resolve())

    # ---- apply: per-type overrides ----

    def test_apply_with_a_per_type_override(self):
        override_target = self.tmp / "loras-elsewhere"

        self.manager.apply(str(self.external), overrides={"loras": str(override_target)})

        loras_link = self.models_root / "loras"
        self.assertEqual(loras_link.resolve(), override_target.resolve())
        checkpoints_link = self.models_root / "checkpoints"
        self.assertEqual(checkpoints_link.resolve(), (self.external / "checkpoints").resolve())

    def test_apply_persists_overrides_setting(self):
        override_target = self.tmp / "loras-elsewhere"

        self.manager.apply(str(self.external), overrides={"loras": str(override_target)})

        setting = self.manager.settings.get_setting_by_key(OVERRIDES_SETTING_KEY)
        self.assertEqual(setting.get_typed_value(), {"loras": str(override_target)})

    # ---- apply: refusals ----

    def test_apply_refuses_a_blank_path(self):
        with self.assertRaises(ModelsLocationError):
            self.manager.apply("   ")

    def test_apply_refuses_when_a_generation_is_active(self):
        manager = ModelsRelocator(
            self.models_root, SettingRepository(), generation_active=lambda: True
        )

        with self.assertRaises(ModelsLocationError) as ctx:
            manager.apply(str(self.external))
        self.assertIn("running", ctx.exception.reason.lower())

    def test_apply_does_not_touch_disk_when_a_generation_is_active(self):
        manager = ModelsRelocator(
            self.models_root, SettingRepository(), generation_active=lambda: True
        )

        with self.assertRaises(ModelsLocationError):
            manager.apply(str(self.external))

        self.assertFalse((self.models_root / "checkpoints").exists())

    def test_apply_refuses_when_windows(self):
        import src.features.models.location as location_module

        original = location_module.os.name
        location_module.os.name = "nt"
        try:
            with self.assertRaises(ModelsLocationError) as ctx:
                self.manager.apply(str(self.external))
        finally:
            location_module.os.name = original

        self.assertIn("windows", ctx.exception.reason.lower())

    def test_apply_refuses_real_files_already_present(self):
        real_dir = self.models_root / "checkpoints"
        real_dir.mkdir(parents=True)
        (real_dir / "existing.safetensors").write_bytes(b"weights")

        with self.assertRaises(ModelsLocationError) as ctx:
            self.manager.apply(str(self.external))

        self.assertIn("checkpoints", ctx.exception.reason)
        # never auto-move: the real file must be untouched
        self.assertTrue((real_dir / "existing.safetensors").exists())

    def test_apply_refuses_real_files_without_symlinking_any_other_directory_either(self):
        """A refusal must not partially apply - other type directories stay untouched."""
        real_dir = self.models_root / "checkpoints"
        real_dir.mkdir(parents=True)
        (real_dir / "existing.safetensors").write_bytes(b"weights")

        with self.assertRaises(ModelsLocationError):
            self.manager.apply(str(self.external))

        self.assertFalse((self.models_root / "loras").exists())

    def test_apply_allows_an_empty_real_directory(self):
        """Only actual file content blocks the switch - an empty directory is safe
        to replace with a symlink."""
        empty_dir = self.models_root / "checkpoints"
        empty_dir.mkdir(parents=True)

        self.manager.apply(str(self.external))  # must not raise

        self.assertTrue((self.models_root / "checkpoints").is_symlink())

    # ---- apply: folder-name alias detection ----

    def test_apply_maps_an_a1111_layout(self):
        sd_dir = self.external / "Stable-diffusion"
        sd_dir.mkdir(parents=True)
        (sd_dir / "model.safetensors").write_bytes(b"weights")

        result = self.manager.apply(str(self.external))

        checkpoints_link = self.models_root / "checkpoints"
        self.assertEqual(checkpoints_link.resolve(), sd_dir.resolve())
        self.assertTrue((checkpoints_link / "model.safetensors").exists())
        self.assertIn("checkpoints", result["auto_matched"])

    def test_apply_maps_a_comfyui_layout(self):
        clip_dir = self.external / "clip"
        clip_dir.mkdir(parents=True)
        (clip_dir / "encoder.safetensors").write_bytes(b"weights")
        upscale_dir = self.external / "upscale_models"
        upscale_dir.mkdir(parents=True)
        (upscale_dir / "upscaler.pth").write_bytes(b"weights")

        result = self.manager.apply(str(self.external))

        self.assertEqual((self.models_root / "text_encoders").resolve(), clip_dir.resolve())
        self.assertEqual((self.models_root / "upscalers").resolve(), upscale_dir.resolve())
        self.assertIn("text_encoders", result["auto_matched"])
        self.assertIn("upscalers", result["auto_matched"])

    def test_apply_maps_a_case_mismatched_folder(self):
        vae_dir = self.external / "VAE"
        vae_dir.mkdir(parents=True)
        (vae_dir / "vae.safetensors").write_bytes(b"weights")

        result = self.manager.apply(str(self.external))

        self.assertEqual((self.models_root / "vae").resolve(), vae_dir.resolve())
        self.assertIn("vae", result["auto_matched"])

    def test_apply_resolves_against_a_nested_models_child(self):
        nested = self.external / "models"
        checkpoints_dir = nested / "checkpoints"
        checkpoints_dir.mkdir(parents=True)
        (checkpoints_dir / "model.safetensors").write_bytes(b"weights")
        loras_dir = nested / "loras"
        loras_dir.mkdir(parents=True)
        (loras_dir / "lora.safetensors").write_bytes(b"weights")

        result = self.manager.apply(str(self.external))

        self.assertEqual((self.models_root / "checkpoints").resolve(), checkpoints_dir.resolve())
        self.assertEqual((self.models_root / "loras").resolve(), loras_dir.resolve())
        self.assertIn("checkpoints", result["auto_matched"])
        self.assertIn("loras", result["auto_matched"])

    def test_apply_explicit_override_beats_alias(self):
        (self.external / "Stable-diffusion").mkdir(parents=True)
        override_target = self.tmp / "checkpoints-elsewhere"
        override_target.mkdir(parents=True)

        result = self.manager.apply(str(self.external), overrides={"checkpoints": str(override_target)})

        checkpoints_link = self.models_root / "checkpoints"
        self.assertEqual(checkpoints_link.resolve(), override_target.resolve())
        self.assertNotIn("checkpoints", result["auto_matched"])
        self.assertEqual(result["overrides"]["checkpoints"], str(override_target))

    def test_apply_exact_name_beats_alias_when_both_have_content(self):
        exact_dir = self.external / "checkpoints"
        exact_dir.mkdir(parents=True)
        (exact_dir / "exact.safetensors").write_bytes(b"weights")
        alias_dir = self.external / "Stable-diffusion"
        alias_dir.mkdir(parents=True)
        (alias_dir / "model.safetensors").write_bytes(b"weights")

        self.manager.apply(str(self.external))

        checkpoints_link = self.models_root / "checkpoints"
        self.assertEqual(checkpoints_link.resolve(), exact_dir.resolve())

    def test_apply_prefers_an_empty_alias_folder_with_real_content_over_an_empty_exact_folder(self):
        (self.external / "loras").mkdir(parents=True)
        lora_dir = self.external / "Lora"
        lora_dir.mkdir(parents=True)
        (lora_dir / "lora.safetensors").write_bytes(b"weights")

        self.manager.apply(str(self.external))

        loras_link = self.models_root / "loras"
        self.assertEqual(loras_link.resolve(), lora_dir.resolve())

    def test_apply_falls_back_to_the_exact_name_when_every_candidate_is_empty(self):
        exact_dir = self.external / "loras"
        exact_dir.mkdir(parents=True)
        (self.external / "Lora").mkdir(parents=True)
        (self.external / "LyCORIS").mkdir(parents=True)

        self.manager.apply(str(self.external))

        loras_link = self.models_root / "loras"
        self.assertEqual(loras_link.resolve(), exact_dir.resolve())

    def test_apply_orders_alias_candidates_deterministically_when_both_have_content(self):
        lora_dir = self.external / "Lora"
        lora_dir.mkdir(parents=True)
        (lora_dir / "lora.safetensors").write_bytes(b"weights")
        lycoris_dir = self.external / "LyCORIS"
        lycoris_dir.mkdir(parents=True)
        (lycoris_dir / "lyco.safetensors").write_bytes(b"weights")

        self.manager.apply(str(self.external))

        loras_link = self.models_root / "loras"
        self.assertEqual(loras_link.resolve(), lora_dir.resolve())

    def test_apply_prefers_a_populated_nested_models_child_over_empty_root_folders(self):
        (self.external / "checkpoints").mkdir(parents=True)
        nested_checkpoints = self.external / "models" / "checkpoints"
        nested_checkpoints.mkdir(parents=True)
        (nested_checkpoints / "model.safetensors").write_bytes(b"weights")

        self.manager.apply(str(self.external))

        checkpoints_link = self.models_root / "checkpoints"
        self.assertEqual(checkpoints_link.resolve(), nested_checkpoints.resolve())

    def test_apply_does_not_map_clip_vision_onto_text_encoders(self):
        clip_vision_dir = self.external / "clip_vision"
        clip_vision_dir.mkdir(parents=True)
        (clip_vision_dir / "vision.safetensors").write_bytes(b"weights")

        result = self.manager.apply(str(self.external))

        text_encoders_link = self.models_root / "text_encoders"
        self.assertEqual(text_encoders_link.resolve(), (self.external / "text_encoders").resolve())
        self.assertIn("text_encoders", result["created_empty"])

    def test_apply_does_not_persist_a_detected_match_as_an_override(self):
        (self.external / "Stable-diffusion").mkdir(parents=True)

        self.manager.apply(str(self.external))
        config = self.manager.get_config()

        self.assertNotIn("checkpoints", config["overrides"])
        checkpoints = next(d for d in config["directories"] if d["directory"] == "checkpoints")
        self.assertEqual(Path(checkpoints["target"]), self.external / "Stable-diffusion")
        self.assertTrue(checkpoints["auto_matched"])

    def test_apply_to_a_new_external_path_does_not_keep_the_old_detected_match(self):
        first = self.tmp / "first"
        (first / "Lora").mkdir(parents=True)
        (first / "Lora" / "a.safetensors").write_bytes(b"weights")
        self.manager.apply(str(first))

        second = self.tmp / "second"
        (second / "loras").mkdir(parents=True)
        (second / "loras" / "b.safetensors").write_bytes(b"weights")
        config = self.manager.get_config()
        self.manager.apply(str(second), overrides=config["overrides"])

        loras_link = self.models_root / "loras"
        self.assertEqual(loras_link.resolve(), (second / "loras").resolve())


class TestModelsRelocatorNoDb:
    """Pure filesystem behavior that doesn't need the settings round-trip."""

    def test_windows_detection_helper(self, tmp_path, monkeypatch):
        manager = ModelsRelocator(tmp_path / "models", SettingRepository())
        monkeypatch.setattr(os, "name", "nt")
        assert manager._is_windows() is True

    def test_apply_refuses_on_windows_before_touching_settings_or_disk(self, tmp_path, monkeypatch):
        manager = ModelsRelocator(tmp_path / "models", SettingRepository())
        monkeypatch.setattr(ModelsRelocator, "_is_windows", staticmethod(lambda: True))

        with pytest.raises(ModelsLocationError) as ctx:
            manager.apply(str(tmp_path / "external"))

        assert "windows" in ctx.value.reason.lower()

    def test_generation_active_defaults_to_false(self, tmp_path):
        manager = ModelsRelocator(tmp_path / "models", SettingRepository())
        assert manager._generation_active() is False
