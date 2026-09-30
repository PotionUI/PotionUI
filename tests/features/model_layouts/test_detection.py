import json
import os
from pathlib import Path

import pytest

from src.features.model_layouts import detection
from src.features.model_layouts.detection import UnknownLayoutError, detect_layout
from src.features.model_layouts.schema import parse_layout
from src.features.models import root_detection
from src.platform.filesystem.model_roots import probe_case_insensitive
from src.platform.filesystem.model_types import binding_scans_headers_by_default
from src.features.model_layouts.wsl import PathTranslator
from tests.features.model_layouts.detection_profiles import PROFILES, catalog, touch

POSIX = PathTranslator(is_windows=False, mount_root="/wsl", is_dir=lambda p: False)


def _subdirs(result, model_type=None):
    return [s["subdir"] for s in result["suggestions"] if model_type is None or s["model_type"] == model_type]


def _by(result, subdir):
    return next(s for s in result["suggestions"] if s["subdir"] == subdir)


def _comfy_tree(base: Path) -> Path:
    touch(base, "folder_paths.py", "comfy/")
    for name in ("checkpoints", "diffusion_models", "unet", "loras", "vae", "text_encoders", "clip", "embeddings"):
        touch(base, f"models/{name}/one.safetensors")
    return base


def _a1111_tree(base: Path, forge=False) -> Path:
    touch(base, "webui.py", "modules/", "embeddings/e.pt")
    if forge:
        touch(base, "modules_forge/")
    for name in ("Stable-diffusion", "VAE", "Lora", "LyCORIS", "ESRGAN"):
        touch(base, f"models/{name}/one.safetensors")
    return base


def _sm_tree(base: Path) -> Path:
    touch(base, "Data/.sm-portable", "Data/Packages/")
    for name in ("StableDiffusion", "DiffusionModels", "TextEncoders", "Lora", "LyCORIS", "VAE", "Embeddings", "ESRGAN"):
        touch(base, f"Data/Models/{name}/one.safetensors")
    return base


class TestComfyUI:
    def test_detected_from_the_install_dir(self, tmp_path):
        _comfy_tree(tmp_path)
        result = detect_layout(str(tmp_path), catalog=catalog())
        assert result["profile"]["id"] == "comfyui" and result["profile"]["confidence"] == "strong"
        assert result["root_path"] == str(tmp_path)
        assert set(_subdirs(result)) == {f"models/{n}" for n in (
            "checkpoints", "diffusion_models", "unet", "loras", "vae", "text_encoders", "clip", "embeddings")}
        assert result["profile"]["install_path"] == str(tmp_path)
        assert result["profile"]["models_path"] == str(tmp_path / "models")
        assert result["layout"] == "typed" and result["outside_folders"] == []

    def test_detected_from_the_models_dir_through_the_suffix_anchor(self, tmp_path):
        _comfy_tree(tmp_path)
        result = detect_layout(str(tmp_path / "models"), catalog=catalog())
        assert result["profile"]["id"] == "comfyui"
        assert result["profile"]["install_path"] == str(tmp_path)
        assert "checkpoints" in _subdirs(result) and "clip" in _subdirs(result)
        assert result["effective_path"] == str(tmp_path / "models")

    def test_unet_and_diffusion_models_both_map_with_header_scanning(self, tmp_path):
        _comfy_tree(tmp_path)
        result = detect_layout(str(tmp_path / "models"), catalog=catalog())
        assert _subdirs(result, "diffusion_model") == ["diffusion_models", "unet"]
        assert _by(result, "unet")["scan_headers"] is True and _by(result, "diffusion_models")["scan_headers"] is True
        assert _by(result, "diffusion_models")["write"] is True and _by(result, "unet")["write"] is False
        assert _by(result, "checkpoints")["scan_headers"] is False
        assert _subdirs(result, "text_encoder") == ["text_encoders", "clip"]

    def test_file_counts_and_source(self, tmp_path):
        _comfy_tree(tmp_path)
        touch(tmp_path, "models/loras/nested/two.safetensors")
        item = _by(detect_layout(str(tmp_path / "models"), catalog=catalog()), "loras")
        assert item["file_count"] == 2 and item["matched_by"] == "profile" and item["source"] == "profile"

    def test_write_falls_back_to_the_first_existing_folder(self, tmp_path):
        touch(tmp_path, "folder_paths.py", "comfy/", "models/unet/a.safetensors", "models/clip/a.safetensors")
        result = detect_layout(str(tmp_path / "models"), catalog=catalog())
        assert _by(result, "unet")["write"] is True and _by(result, "clip")["write"] is True

    def test_install_dir_one_level_down_covers_the_portable_wrapper(self, tmp_path):
        _comfy_tree(tmp_path / "ComfyUI")
        result = detect_layout(str(tmp_path), catalog=catalog())
        assert result["profile"]["id"] == "comfyui"
        assert result["profile"]["install_path"] == str(tmp_path / "ComfyUI")
        assert "ComfyUI/models/checkpoints" in _subdirs(result)


class TestA1111:
    def test_install_dir_includes_the_root_level_embeddings(self, tmp_path):
        _a1111_tree(tmp_path)
        result = detect_layout(str(tmp_path), catalog=catalog())
        assert result["profile"]["id"] == "a1111"
        assert "embeddings" in _subdirs(result, "embedding")
        assert set(_subdirs(result, "lora")) == {"models/Lora", "models/LyCORIS"}
        assert _by(result, "models/Stable-diffusion")["scan_headers"] is True

    def test_models_dir_offers_the_outside_embeddings_folder(self, tmp_path):
        _a1111_tree(tmp_path)
        result = detect_layout(str(tmp_path / "models"), catalog=catalog())
        assert result["profile"]["id"] == "a1111"
        assert result["outside_folders"] == [
            {"model_type": "embedding", "path": str(tmp_path / "embeddings"), "label": "embeddings",
             "install_path": str(tmp_path)}
        ]
        assert _subdirs(result, "embedding") == []
        assert set(_subdirs(result, "lora")) == {"Lora", "LyCORIS"}

    def test_forge_variant_refines_the_label(self, tmp_path):
        _a1111_tree(tmp_path, forge=True)
        assert detect_layout(str(tmp_path), catalog=catalog())["profile"]["variant"] == "Forge"
        plain = tmp_path.parent / (tmp_path.name + "-plain")
        _a1111_tree(plain)
        assert detect_layout(str(plain), catalog=catalog())["profile"]["variant"] is None

    def test_commandline_args_add_folders_and_an_extra_root(self, tmp_path):
        install = _a1111_tree(tmp_path / "webui")
        shared = tmp_path / "shared" / "ckpt"
        touch(shared, "big.safetensors")
        (install / "webui-user.sh").write_text(f'export COMMANDLINE_ARGS="--ckpt-dir {shared.as_posix()}"\n', encoding="utf-8")
        result = detect_layout(str(install), catalog=catalog("a1111"))
        assert len(result["extra_roots"]) == 1
        extra = result["extra_roots"][0]
        assert Path(extra["path"]) == shared.parent
        assert [s["subdir"] for s in extra["suggestions"]] == ["ckpt"]
        assert extra["suggestions"][0]["model_type"] == "checkpoint" and extra["source"].startswith("webui-user.sh")
        assert extra["primary"] is False and extra["profile_id"] == "a1111"

    def test_commandline_args_inside_the_root_become_config_suggestions(self, tmp_path):
        install = _a1111_tree(tmp_path)
        touch(install, "extra_ckpt/x.safetensors")
        (install / "webui-user.sh").write_text('export COMMANDLINE_ARGS="--ckpt-dir extra_ckpt"\n', encoding="utf-8")
        result = detect_layout(str(install), catalog=catalog("a1111"))
        extra = _by(result, "extra_ckpt")
        assert extra["matched_by"] == "config" and extra["source"] == "config" and result["extra_roots"] == []


class TestStabilityMatrix:
    def test_portable_app_dir_finds_data_models_with_lora_and_lycoris(self, tmp_path):
        _sm_tree(tmp_path)
        result = detect_layout(str(tmp_path), catalog=catalog())
        assert result["profile"]["id"] == "stabilitymatrix" and result["profile"]["confidence"] == "strong"
        assert result["profile"]["install_path"] == str(tmp_path / "Data")
        assert set(_subdirs(result, "lora")) == {"Data/Models/Lora", "Data/Models/LyCORIS"}
        assert _subdirs(result, "checkpoint") == ["Data/Models/StableDiffusion"]
        assert _subdirs(result, "diffusion_model") == ["Data/Models/DiffusionModels"]
        assert _subdirs(result, "text_encoder") == ["Data/Models/TextEncoders"]
        assert _by(result, "Data/Models/StableDiffusion")["scan_headers"] is True
        assert _by(result, "Data/Models/Lora")["write"] is True and _by(result, "Data/Models/LyCORIS")["write"] is False

    def test_models_dir_uses_the_suffix_anchor(self, tmp_path):
        _sm_tree(tmp_path)
        result = detect_layout(str(tmp_path / "Data" / "Models"), catalog=catalog())
        assert result["profile"]["id"] == "stabilitymatrix"
        assert set(_subdirs(result, "lora")) == {"Lora", "LyCORIS"}
        assert "StableDiffusion" in _subdirs(result)

    def test_generic_override_gives_todays_partial_result(self, tmp_path):
        _sm_tree(tmp_path)
        models = str(tmp_path / "Data" / "Models")
        generic = detect_layout(models, catalog=catalog(), profile="generic")
        expected = root_detection.detect(models).to_dict()
        assert generic["profile"] is None
        assert [(s["model_type"], s["subdir"]) for s in generic["suggestions"]] == [
            (s["model_type"], s["subdir"]) for s in expected["suggestions"]
        ]
        assert "StableDiffusion" not in _subdirs(generic)
        assert len(detect_layout(models, catalog=catalog())["suggestions"]) > len(generic["suggestions"])

    def test_override_directory_becomes_a_primary_extra_root(self, tmp_path):
        install = tmp_path / "sm"
        touch(install, "Data/.sm-portable", "Data/Packages/")
        big = tmp_path / "big"
        touch(big, "StableDiffusion/a.safetensors", "Lora/l.safetensors", "LyCORIS/y.safetensors")
        (install / "Data" / "settings.json").write_text(json.dumps({"ModelDirectoryOverride": str(big)}), encoding="utf-8")
        result = detect_layout(str(install), catalog=catalog("stabilitymatrix"))
        assert result["suggestions"] == []
        extra = result["extra_roots"][0]
        assert extra["primary"] is True and Path(extra["path"]) == big
        assert {s["subdir"] for s in extra["suggestions"]} == {"StableDiffusion", "Lora", "LyCORIS"}

    def test_override_inside_the_typed_path_replaces_the_models_root(self, tmp_path):
        touch(tmp_path, "Data/.sm-portable", "Data/Packages/", "mine/Lora/l.safetensors")
        (tmp_path / "Data" / "settings.json").write_text(json.dumps({"ModelDirectoryOverride": str(tmp_path / "mine")}), encoding="utf-8")
        result = detect_layout(str(tmp_path), catalog=catalog("stabilitymatrix"))
        assert _subdirs(result) == ["mine/Lora"]
        assert result["extra_roots"] == []


class TestOtherTools:
    def test_fooocus_config_outside_folder_is_an_extra_root(self, tmp_path):
        install = tmp_path / "fooocus"
        touch(install, "entry_with_update.py", "models/loras/l.safetensors")
        elsewhere = tmp_path / "elsewhere" / "ck"
        touch(elsewhere, "c.safetensors")
        (install / "config.txt").write_text(json.dumps({"path_checkpoints": str(elsewhere)}), encoding="utf-8")
        result = detect_layout(str(install), catalog=catalog())
        assert result["profile"]["id"] == "fooocus"
        assert _subdirs(result) == ["models/loras"]
        assert Path(result["extra_roots"][0]["path"]) == elsewhere.parent

    def test_swarm_extra_model_roots_are_offered(self, tmp_path):
        install = tmp_path / "swarm"
        touch(install, "launch-linux.sh", "src/", "Models/Lora/l.safetensors")
        second = tmp_path / "second"
        touch(second, "Stable-Diffusion/s.safetensors")
        (install / "Data").mkdir()
        (install / "Data" / "Settings.fds").write_text(f"Paths:\n\tModelRoot: Models;{second}\n", encoding="utf-8")
        result = detect_layout(str(install), catalog=catalog())
        assert result["profile"]["id"] == "swarmui"
        assert _subdirs(result) == ["Models/Lora"]
        extra = result["extra_roots"][0]
        assert Path(extra["path"]) == second and extra["primary"] is False
        assert [s["subdir"] for s in extra["suggestions"]] == ["Stable-Diffusion"]

    def test_comfy_yaml_sections_become_extra_roots_and_missing_paths_warn(self, tmp_path):
        install = _comfy_tree(tmp_path / "comfy")
        other = tmp_path / "a1111"
        touch(other, "models/Lora/l.safetensors", "models/Stable-diffusion/s.safetensors")
        yaml_text = (
            f"a111:\n    base_path: {other.as_posix()}\n    checkpoints: models/Stable-diffusion\n    loras: models/Lora\n"
            f"gone:\n    base_path: {(tmp_path / 'gone').as_posix()}\n    vae: v\n"
        )
        (install / "extra_model_paths.yaml").write_text(yaml_text, encoding="utf-8")
        result = detect_layout(str(install), catalog=catalog("comfyui"))
        assert len(result["extra_roots"]) == 1
        extra = result["extra_roots"][0]
        assert Path(extra["path"]) == other / "models"
        assert extra["source"] == "extra_model_paths.yaml › a111"
        assert {(s["model_type"], s["subdir"]) for s in extra["suggestions"]} == {
            ("checkpoint", "Stable-diffusion"), ("lora", "Lora")}
        assert any("does not exist" in w for w in result["warnings"])


class TestRankingAndOverride:
    def test_strong_marker_match_beats_a_folder_only_match(self, tmp_path):
        touch(tmp_path, "webui.py", "modules/", "models/Stable-diffusion/a.safetensors")
        touch(tmp_path, "models/checkpoints/a.safetensors", "models/loras/a.safetensors", "models/vae/a.safetensors")
        result = detect_layout(str(tmp_path), catalog=catalog("comfyui", "a1111"))
        assert result["profile"]["id"] == "a1111"
        assert [a["id"] for a in result["alternatives"]] == ["comfyui", "generic"]
        assert result["alternatives"][0]["confidence"] == "weak"

    def test_a_strong_match_beats_a_weak_one_with_a_higher_total(self, tmp_path):
        touch(tmp_path, "marker.txt", "models/loras/a.safetensors", "models/vae/a.safetensors", "models/checkpoints/a.safetensors")
        strong = {
            "schema": 1, "id": "strong", "label": "S", "markers": [{"path": "marker.txt", "weight": 4}],
            "min_marker_score": 4, "folders": [{"path": "loras", "model_type": "lora"}],
        }
        weak = {
            "schema": 1, "id": "weak", "label": "W", "markers": [{"path": "nothing.txt"}], "priority": 99,
            "folders": [
                {"path": "loras", "model_type": "lora", "weight": 5},
                {"path": "vae", "model_type": "vae", "weight": 5},
                {"path": "checkpoints", "model_type": "checkpoint", "weight": 5},
            ],
        }
        result = detect_layout(str(tmp_path), catalog=_cat([weak, strong]))
        assert result["profile"]["id"] == "strong"
        assert result["alternatives"][0]["id"] == "weak" and result["alternatives"][0]["score"] > result["profile"]["score"]

    def test_folder_only_match_is_weak(self, tmp_path):
        touch(tmp_path, "models/checkpoints/a.safetensors", "models/loras/a.safetensors", "models/vae/a.safetensors")
        result = detect_layout(str(tmp_path), catalog=catalog("comfyui"))
        assert result["profile"]["id"] == "comfyui" and result["profile"]["confidence"] == "weak"

    def test_match_on_folders_false_never_matches_on_folders_alone(self, tmp_path):
        data = dict(PROFILES["comfyui"], match_on_folders=False)
        touch(tmp_path, "models/checkpoints/a.safetensors", "models/loras/a.safetensors", "models/vae/a.safetensors")
        result = detect_layout(str(tmp_path), catalog=_one(data))
        assert result["profile"] is None

    def test_ties_break_by_priority_then_id(self, tmp_path):
        touch(tmp_path, "marker.txt", "models/loras/a.safetensors")
        base = {
            "schema": 1, "label": "T", "markers": [{"path": "marker.txt", "weight": 4}], "min_marker_score": 4,
            "folders": [{"path": "loras", "model_type": "lora"}],
        }
        cat = _cat([dict(base, id="bbb", priority=50), dict(base, id="aaa", priority=50), dict(base, id="ccc", priority=70)])
        result = detect_layout(str(tmp_path), catalog=cat)
        assert result["profile"]["id"] == "ccc"
        assert [a["id"] for a in result["alternatives"]] == ["aaa", "bbb", "generic"]

    def test_higher_score_beats_priority(self, tmp_path):
        touch(tmp_path, "marker.txt", "m2.txt", "models/loras/a.safetensors")
        base = {"schema": 1, "label": "T", "min_marker_score": 4, "folders": [{"path": "loras", "model_type": "lora"}]}
        low = dict(base, id="low", priority=90, markers=[{"path": "marker.txt", "weight": 4}])
        high = dict(base, id="high", priority=10, markers=[{"path": "marker.txt", "weight": 4}, {"path": "m2.txt", "weight": 3}])
        assert detect_layout(str(tmp_path), catalog=_cat([low, high]))["profile"]["id"] == "high"

    def test_forced_profile_applies_even_without_markers_and_warns(self, tmp_path):
        touch(tmp_path, "models/checkpoints/a.safetensors")
        result = detect_layout(str(tmp_path), catalog=catalog(), profile="comfyui")
        assert result["profile"]["id"] == "comfyui" and result["profile"]["confidence"] is None
        assert "No ComfyUI markers found here" in result["warnings"]
        assert _subdirs(result) == ["models/checkpoints"]

    def test_forced_profile_that_matches_does_not_warn(self, tmp_path):
        _comfy_tree(tmp_path)
        result = detect_layout(str(tmp_path), catalog=catalog(), profile="comfyui")
        assert not any("markers" in w for w in result["warnings"])

    def test_unknown_profile_raises(self, tmp_path):
        with pytest.raises(UnknownLayoutError) as caught:
            detect_layout(str(tmp_path), catalog=catalog(), profile="nope")
        assert caught.value.code == "model_layout_unknown"

    def test_offline_path_reports_state_without_a_profile(self, tmp_path):
        result = detect_layout(str(tmp_path / "missing"), catalog=catalog())
        assert result["state"] == "offline" and result["profile"] is None and result["suggestions"] == []


class TestNestedInstalls:
    def test_swarm_wins_over_the_comfy_inside_it(self, tmp_path):
        touch(tmp_path, "launch-linux.sh", "src/", "Models/Stable-Diffusion/a.safetensors")
        _comfy_tree(tmp_path / "dlbackend" / "comfy" / "ComfyUI")
        result = detect_layout(str(tmp_path), catalog=catalog())
        assert result["profile"]["id"] == "swarmui"
        assert _subdirs(result) == ["Models/Stable-Diffusion"]

    def test_typing_the_inner_install_resolves_the_inner_tool(self, tmp_path):
        touch(tmp_path, "launch-linux.sh", "src/")
        inner = _comfy_tree(tmp_path / "dlbackend" / "comfy" / "ComfyUI")
        assert detect_layout(str(inner), catalog=catalog())["profile"]["id"] == "comfyui"


class TestPinokio:
    def _home(self, base: Path) -> Path:
        touch(base, "drive/", "api/comfy.git/app/folder_paths.py", "api/comfy.git/app/comfy/")
        touch(base, "api/comfy.git/app/models/checkpoints/a.safetensors", "api/comfy.git/app/models/loras/l.safetensors")
        _a1111_tree(base / "api" / "forge.git" / "app", forge=True)
        touch(base, "api/notes/readme.txt")
        return base

    def test_home_delegates_to_its_apps_and_picks_the_best(self, tmp_path):
        home = self._home(tmp_path)
        result = detect_layout(str(home), catalog=catalog())
        assert result["profile"]["id"] == "pinokio"
        assert result["path"] == str(home)
        assert {d["label"] for d in result["delegated"]} == {"app"}
        assert {(Path(d["path"]).parent.name, d["profile"]["id"]) for d in result["delegated"]} == {
            ("comfy.git", "comfyui"), ("forge.git", "a1111")}
        assert result["root_path"] == result["delegated"][0]["path"]
        assert result["suggestions"]

    def test_the_first_delegate_is_the_highest_scoring(self, tmp_path):
        result = detect_layout(str(self._home(tmp_path)), catalog=catalog())
        scores = [d["profile"]["id"] for d in result["delegated"]]
        assert scores[0] == "a1111"
        assert Path(result["root_path"]).parent.name == "forge.git"

    def test_typing_an_app_dir_detects_the_tool_directly(self, tmp_path):
        home = self._home(tmp_path)
        result = detect_layout(str(home / "api" / "comfy.git" / "app"), catalog=catalog())
        assert result["profile"]["id"] == "comfyui" and result["delegated"] == []

    def test_home_without_apps_warns(self, tmp_path):
        touch(tmp_path, "drive/", "api/notes/readme.txt")
        result = detect_layout(str(tmp_path), catalog=catalog())
        assert result["profile"]["id"] == "pinokio" and result["delegated"] == []
        assert any("No installed apps" in w for w in result["warnings"])

    def test_delegate_candidates_are_capped(self, tmp_path):
        touch(tmp_path, "drive/")
        for index in range(12):
            _comfy_tree(tmp_path / "api" / f"app{index:02d}.git" / "app")
        result = detect_layout(str(tmp_path), catalog=catalog())
        assert len(result["delegated"]) == 8

    def test_linked_apps_are_deduplicated_by_realpath(self, tmp_path):
        touch(tmp_path, "drive/")
        real = _comfy_tree(tmp_path / "drive" / "real")
        (tmp_path / "api" / "one.git").mkdir(parents=True)
        try:
            os.symlink(real, tmp_path / "api" / "one.git" / "app", target_is_directory=True)
            os.symlink(real, tmp_path / "api" / "two.git", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
        result = detect_layout(str(tmp_path), catalog=catalog())
        assert len(result["delegated"]) == 1


class TestGenericFallback:
    def test_unknown_tree_returns_exactly_todays_result(self, tmp_path):
        touch(tmp_path, "stable-diffusion/a.safetensors", "Lora/l.safetensors", "misc/readme.txt")
        result = detect_layout(str(tmp_path), catalog=catalog())
        expected = root_detection.detect(str(tmp_path)).to_dict()
        assert result["profile"] is None and result["root_path"] == str(tmp_path)
        for key, value in expected.items():
            if key == "suggestions":
                assert [(s["model_type"], s["subdir"], s["matched_by"], s["file_count"]) for s in result[key]] == [
                    (s["model_type"], s["subdir"], s["matched_by"], s["file_count"]) for s in value]
            else:
                assert result[key] == value
        assert result["alternatives"] == [{"id": "generic", "label": "Generic (folder names)", "confidence": None, "score": 0}]
        assert result["outside_folders"] == [] and result["extra_roots"] == [] and result["delegated"] == []

    def test_generic_suggestions_carry_label_write_and_source(self, tmp_path):
        touch(tmp_path, "Lora/l.safetensors", "LyCORIS/y.safetensors")
        result = detect_layout(str(tmp_path), catalog=None)
        by = {s["subdir"]: s for s in result["suggestions"]}
        assert by["Lora"]["label"] == "Lora" and by["Lora"]["source"] == "generic" and by["Lora"]["write"] is True
        assert result["suggestions"][0]["scan_headers"] is binding_scans_headers_by_default("lora", "Lora")

    def test_single_folder_layout_is_the_generic_single_result(self, tmp_path):
        touch(tmp_path, "one.safetensors")
        result = detect_layout(str(tmp_path), catalog=catalog())
        assert result["layout"] == "single" and result["profile"] is None

    def test_empty_catalog_is_generic(self, tmp_path):
        _comfy_tree(tmp_path)
        assert detect_layout(str(tmp_path), catalog=_cat([]))["profile"] is None


class TestBudgetAndCounting:
    def test_exhausted_budget_skips_counting_and_flags_truncation(self, tmp_path):
        _comfy_tree(tmp_path)
        ticks = iter(range(0, 10_000, 10))
        result = detect_layout(str(tmp_path), catalog=catalog(), clock=lambda: next(ticks))
        assert result["suggestions"]
        assert all(s["file_count"] == 0 and s["file_count_truncated"] is True for s in result["suggestions"])

    def test_generous_budget_counts_files(self, tmp_path):
        _comfy_tree(tmp_path)
        result = detect_layout(str(tmp_path), catalog=catalog(), clock=lambda: 0.0)
        assert all(s["file_count"] == 1 and s["file_count_truncated"] is False for s in result["suggestions"])

    def test_presence_scanning_stops_at_the_deadline(self, tmp_path):
        touch(tmp_path, "folder_paths.py", "comfy/")
        for index in range(60):
            (tmp_path / "models" / "loras" / f"d{index:02d}").mkdir(parents=True)
        touch(tmp_path, "models/loras/zz/deep.safetensors")
        generous = detect_layout(str(tmp_path), catalog=catalog("comfyui"), clock=lambda: 0.0)
        ticks = iter(range(0, 100_000, 10))
        exhausted = detect_layout(str(tmp_path), catalog=catalog("comfyui"), clock=lambda: next(ticks))
        assert generous["profile"]["score"] == exhausted["profile"]["score"] + 1

    def test_only_the_chosen_profile_is_counted(self, tmp_path, monkeypatch):
        _comfy_tree(tmp_path)
        touch(tmp_path, "webui.py", "modules/")
        calls = []
        real = detection._count_files
        monkeypatch.setattr(detection, "_count_files", lambda run, d: calls.append(d) or real(run, d))
        detect_layout(str(tmp_path), catalog=catalog("comfyui", "a1111"))
        assert calls and all("models" in Path(c).parts for c in calls)
        assert len(calls) == 8


class TestConflictsAndCase:
    class _Resolver:
        def __init__(self, bindings):
            self._bindings = bindings

        def type_dirs(self, model_type, online_only=True):
            return [type("TD", (), {"root_id": r, "path": p})() for r, mt, p in self._bindings if mt == model_type]

    def test_overlap_with_an_existing_root_is_reported(self, tmp_path):
        _comfy_tree(tmp_path)
        resolver = self._Resolver([("root1", "lora", tmp_path / "models" / "loras")])
        result = detect_layout(str(tmp_path), catalog=catalog(), resolver=resolver)
        assert result["conflicts"] and result["conflicts"][0]["root_id"] == "root1"

    def test_a_link_to_an_existing_root_is_reported(self, tmp_path):
        real = tmp_path / "real_loras"
        touch(real, "l.safetensors")
        _comfy_tree(tmp_path / "comfy")
        linked = tmp_path / "comfy" / "models" / "loras"
        for child in linked.iterdir():
            child.unlink()
        linked.rmdir()
        try:
            os.symlink(real, linked, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
        resolver = self._Resolver([("root9", "lora", real)])
        result = detect_layout(str(tmp_path / "comfy"), catalog=catalog(), resolver=resolver)
        assert any("via a link" in c["reason"] for c in result["conflicts"])

    def test_two_folders_linked_to_one_target_collapse_to_the_first(self, tmp_path):
        shared = tmp_path / "shared"
        touch(shared, "l.safetensors")
        touch(tmp_path, "webui.py", "modules/")
        (tmp_path / "models").mkdir()
        try:
            os.symlink(shared, tmp_path / "models" / "Lora", target_is_directory=True)
            os.symlink(shared, tmp_path / "models" / "LyCORIS", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
        result = detect_layout(str(tmp_path), catalog=catalog("a1111"))
        assert _subdirs(result, "lora") == ["models/Lora"]

    def test_case_variants_on_a_case_sensitive_filesystem_are_all_offered(self, tmp_path):
        if probe_case_insensitive(tmp_path):
            pytest.skip("case-insensitive filesystem")
        touch(tmp_path, "webui.py", "modules/", "models/VAE/a.safetensors", "models/vae/b.safetensors")
        result = detect_layout(str(tmp_path), catalog=catalog("a1111"))
        assert set(_subdirs(result, "vae")) == {"models/VAE", "models/vae"}
        assert _by(result, "models/VAE")["write"] is True
        assert any("more than one spelling" in w for w in result["warnings"])

    def test_empty_case_variant_is_ignored(self, tmp_path):
        if probe_case_insensitive(tmp_path):
            pytest.skip("case-insensitive filesystem")
        touch(tmp_path, "webui.py", "modules/", "models/VAE/a.safetensors", "models/vae/")
        assert _subdirs(detect_layout(str(tmp_path), catalog=catalog("a1111")), "vae") == ["models/VAE"]


def _cat(datas):
    return type(catalog())([parse_layout(d) for d in datas])


def _one(data):
    return _cat([data])
