import os
from pathlib import Path

import pytest

from src.features.models import root_detection
from src.features.models.root_detection import DetectedLayout, detect, resolve_targets


class FakeTypeDir:
    def __init__(self, root_id, path):
        self.root_id = root_id
        self.path = path


class FakeResolver:
    def __init__(self, bindings):
        self._bindings = bindings

    def type_dirs(self, model_type, online_only=True):
        return [FakeTypeDir(root_id, path) for root_id, mt, path in self._bindings if mt == model_type]


class TestResolveTargets:
    def test_maps_an_a1111_layout(self, tmp_path):
        sd_dir = tmp_path / "Stable-diffusion"
        sd_dir.mkdir()
        (sd_dir / "model.safetensors").write_bytes(b"weights")

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["checkpoints"].path == sd_dir
        assert resolved["checkpoints"].matched is True

    def test_maps_a_comfyui_layout(self, tmp_path):
        clip_dir = tmp_path / "clip"
        clip_dir.mkdir()
        (clip_dir / "encoder.safetensors").write_bytes(b"weights")
        upscale_dir = tmp_path / "upscale_models"
        upscale_dir.mkdir()
        (upscale_dir / "upscaler.pth").write_bytes(b"weights")

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["text_encoders"].path == clip_dir
        assert resolved["upscalers"].path == upscale_dir

    def test_maps_a_case_mismatched_folder(self, tmp_path):
        vae_dir = tmp_path / "VAE"
        vae_dir.mkdir()
        (vae_dir / "vae.safetensors").write_bytes(b"weights")

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["vae"].path == vae_dir

    def test_resolves_against_a_nested_models_child(self, tmp_path):
        nested = tmp_path / "models"
        checkpoints_dir = nested / "checkpoints"
        checkpoints_dir.mkdir(parents=True)
        (checkpoints_dir / "model.safetensors").write_bytes(b"weights")
        loras_dir = nested / "loras"
        loras_dir.mkdir(parents=True)
        (loras_dir / "lora.safetensors").write_bytes(b"weights")

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["checkpoints"].path == checkpoints_dir
        assert resolved["loras"].path == loras_dir

    def test_explicit_override_beats_alias(self, tmp_path):
        (tmp_path / "Stable-diffusion").mkdir()
        override_target = tmp_path / "checkpoints-elsewhere"
        override_target.mkdir()

        resolved = resolve_targets(str(tmp_path), {"checkpoints": str(override_target)})

        assert resolved["checkpoints"].path == override_target

    def test_exact_name_beats_alias_when_both_have_content(self, tmp_path):
        exact_dir = tmp_path / "checkpoints"
        exact_dir.mkdir()
        (exact_dir / "exact.safetensors").write_bytes(b"weights")
        alias_dir = tmp_path / "Stable-diffusion"
        alias_dir.mkdir()
        (alias_dir / "model.safetensors").write_bytes(b"weights")

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["checkpoints"].path == exact_dir

    def test_prefers_a_populated_alias_folder_over_an_empty_exact_folder(self, tmp_path):
        (tmp_path / "loras").mkdir()
        lora_dir = tmp_path / "Lora"
        lora_dir.mkdir()
        (lora_dir / "lora.safetensors").write_bytes(b"weights")

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["loras"].path == lora_dir

    def test_falls_back_to_the_exact_name_when_every_candidate_is_empty(self, tmp_path):
        exact_dir = tmp_path / "loras"
        exact_dir.mkdir()
        (tmp_path / "Lora").mkdir()
        (tmp_path / "LyCORIS").mkdir()

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["loras"].path == exact_dir

    def test_orders_alias_candidates_deterministically_when_both_have_content(self, tmp_path):
        lora_dir = tmp_path / "Lora"
        lora_dir.mkdir()
        (lora_dir / "lora.safetensors").write_bytes(b"weights")
        lycoris_dir = tmp_path / "LyCORIS"
        lycoris_dir.mkdir()
        (lycoris_dir / "lyco.safetensors").write_bytes(b"weights")

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["loras"].path == lora_dir

    def test_prefers_a_populated_nested_models_child_over_empty_root_folders(self, tmp_path):
        (tmp_path / "checkpoints").mkdir()
        nested_checkpoints = tmp_path / "models" / "checkpoints"
        nested_checkpoints.mkdir(parents=True)
        (nested_checkpoints / "model.safetensors").write_bytes(b"weights")

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["checkpoints"].path == nested_checkpoints

    def test_does_not_map_clip_vision_onto_text_encoders(self, tmp_path):
        clip_vision_dir = tmp_path / "clip_vision"
        clip_vision_dir.mkdir()
        (clip_vision_dir / "vision.safetensors").write_bytes(b"weights")

        resolved = resolve_targets(str(tmp_path), {})

        assert resolved["text_encoders"].path == tmp_path / "text_encoders"
        assert resolved["text_encoders"].matched is False


class TestDetectLayout:
    def test_detect_offline_for_a_missing_path(self, tmp_path):
        result = detect(str(tmp_path / "does-not-exist"))

        assert result.state == "offline"
        assert result.layout == "empty"
        assert result.suggestions == []

    def test_detect_typed_layout_for_a_comfyui_folder(self, tmp_path):
        loras_dir = tmp_path / "loras"
        loras_dir.mkdir()
        (loras_dir / "a.safetensors").write_bytes(b"weights")
        checkpoints_dir = tmp_path / "checkpoints"
        checkpoints_dir.mkdir()
        (checkpoints_dir / "b.safetensors").write_bytes(b"weights")

        result = detect(str(tmp_path))

        assert result.state == "online"
        assert result.layout == "typed"
        types = {s.model_type for s in result.suggestions}
        assert {"lora", "checkpoint"} <= types
        lora_suggestion = next(s for s in result.suggestions if s.model_type == "lora")
        assert lora_suggestion.subdir == "loras"
        assert lora_suggestion.matched_by == "canonical"
        assert lora_suggestion.file_count == 1
        assert lora_suggestion.file_count_truncated is False

    def test_detect_typed_layout_for_an_a1111_folder(self, tmp_path):
        sd_dir = tmp_path / "Stable-diffusion"
        sd_dir.mkdir()
        (sd_dir / "model.safetensors").write_bytes(b"weights")

        result = detect(str(tmp_path))

        suggestion = next(s for s in result.suggestions if s.model_type == "checkpoint")
        assert suggestion.subdir == "Stable-diffusion"
        assert suggestion.matched_by == "alias"

    def test_detect_resolves_against_a_nested_models_child(self, tmp_path):
        nested = tmp_path / "models" / "loras"
        nested.mkdir(parents=True)
        (nested / "a.safetensors").write_bytes(b"weights")

        result = detect(str(tmp_path))

        assert result.effective_path == str(tmp_path / "models")
        suggestion = next(s for s in result.suggestions if s.model_type == "lora")
        assert suggestion.subdir == "models/loras"

    def test_detect_case_insensitive_folder_match(self, tmp_path):
        vae_dir = tmp_path / "VAE"
        vae_dir.mkdir()
        (vae_dir / "vae.safetensors").write_bytes(b"weights")

        result = detect(str(tmp_path))

        suggestion = next(s for s in result.suggestions if s.model_type == "vae")
        assert suggestion.subdir == "VAE"

    def test_detect_single_type_folder(self, tmp_path):
        (tmp_path / "a.safetensors").write_bytes(b"weights")

        result = detect(str(tmp_path))

        assert result.layout == "single"
        assert result.suggestions == []

    def test_detect_single_type_guess_from_folder_name(self, tmp_path):
        single_dir = tmp_path / "Lora"
        single_dir.mkdir()
        (single_dir / "a.safetensors").write_bytes(b"weights")

        result = detect(str(single_dir))

        assert result.layout == "single"
        assert result.single_type_guess == "lora"

    def test_detect_empty_folder_has_no_suggestions(self, tmp_path):
        result = detect(str(tmp_path))

        assert result.layout == "empty"
        assert result.suggestions == []
        assert result.single_type_guess is None

    def test_detect_truncates_file_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr(root_detection, "_FILE_COUNT_LIMIT", 2)
        loras_dir = tmp_path / "loras"
        loras_dir.mkdir()
        for i in range(5):
            (loras_dir / f"m{i}.safetensors").write_bytes(b"weights")

        result = detect(str(tmp_path))

        suggestion = next(s for s in result.suggestions if s.model_type == "lora")
        assert suggestion.file_count_truncated is True
        assert suggestion.file_count == 2

    def test_detect_reports_conflicts_against_existing_bindings(self, tmp_path):
        loras_dir = tmp_path / "loras"
        loras_dir.mkdir()
        (loras_dir / "a.safetensors").write_bytes(b"weights")
        resolver = FakeResolver([("home", "lora", loras_dir)])

        result = detect(str(tmp_path), resolver=resolver)

        assert any(c.root_id == "home" for c in result.conflicts)

    def test_detect_no_conflicts_without_a_resolver(self, tmp_path):
        loras_dir = tmp_path / "loras"
        loras_dir.mkdir()

        result = detect(str(tmp_path))

        assert result.conflicts == []

    def test_detect_warns_about_an_invisible_mapped_drive_when_offline(self):
        result = detect("Z:\\ComfyUI")

        assert result.state == "offline"
        assert any("Z:" in w for w in result.warnings)

    def test_to_dict_round_trips_the_shape(self, tmp_path):
        (tmp_path / "loras").mkdir()

        result = detect(str(tmp_path))
        data = result.to_dict()

        assert set(data.keys()) == {
            "path", "effective_path", "state", "writable_hint", "case_insensitive",
            "layout", "suggestions", "single_type_guess", "conflicts", "warnings",
        }


class TestLatentUpscaleAlias:
    def test_latent_upscale_models_folder_is_an_upscaler_folder(self):
        from src.platform.filesystem.model_types import type_for_folder_name

        assert type_for_folder_name("latent_upscale_models") == "upscaler"
        assert type_for_folder_name("Latent_Upscale_Models") == "upscaler"

    def test_detect_binds_it_when_it_is_the_only_upscaler_folder(self, tmp_path):
        folder = tmp_path / "latent_upscale_models"
        folder.mkdir()
        (folder / "ltx_latent_upscaler.safetensors").write_bytes(b"weights")

        result = detect(str(tmp_path))

        assert [(s.model_type, s.subdir) for s in result.suggestions] == [("upscaler", "latent_upscale_models")]
