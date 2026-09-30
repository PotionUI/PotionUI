import json
from pathlib import Path

import pytest

from src.features.model_layouts.catalog import scan_layout_root
from src.features.model_layouts.detection import detect_layout
from tests.features.model_layouts.detection_profiles import FakeCatalog, touch

ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = ROOT / "content" / "model-layouts" / "marketplace"
EXPECTED_IDS = {"a1111", "comfyui", "fooocus", "pinokio", "stabilitymatrix", "swarmui"}


@pytest.fixture(scope="module")
def shipped():
    layouts, errors = {}, {}
    scan_layout_root(MARKETPLACE, "marketplace", layouts, errors)
    return layouts, errors


@pytest.fixture(scope="module")
def real_catalog(shipped):
    return FakeCatalog(list(shipped[0].values()))


def _pairs(result):
    return {(s["model_type"], s["subdir"]) for s in result["suggestions"]}


def _sug(result, subdir):
    return next(s for s in result["suggestions"] if s["subdir"] == subdir)


def _files(base: Path, *folders: str) -> None:
    for folder in folders:
        touch(base, f"{folder}/one.safetensors")


class TestShippedProfiles:
    def test_every_shipped_profile_lints_clean(self, shipped):
        layouts, errors = shipped
        assert errors == {}
        assert set(layouts) == EXPECTED_IDS

    def test_every_profile_cites_sources(self, shipped):
        assert all(layout.sources for layout in shipped[0].values())

    def test_every_profile_references_only_implemented_config_readers(self, shipped):
        kinds = {ref.kind for layout in shipped[0].values() for ref in layout.config_readers}
        assert kinds == {
            "comfyui_extra_model_paths", "a1111_commandline_args", "sdnext_config_json",
            "stabilitymatrix_settings_json", "fooocus_config_txt", "swarmui_settings_fds",
        }

    def test_unet_folders_map_to_diffusion_model_with_header_scanning(self, shipped):
        for layout_id, folder_path in (("comfyui", "unet"), ("swarmui", "unet"), ("a1111", "UNET"), ("stabilitymatrix", "Unet")):
            folder = next(f for f in shipped[0][layout_id].folders if f.path == folder_path)
            assert folder.model_type == "diffusion_model" and folder.scan_headers is True

    def test_the_repo_ships_no_layout_that_breaks_the_lint_script(self):
        import subprocess
        import sys

        done = subprocess.run(
            [sys.executable, "scripts/model_layout_lint.py", str(MARKETPLACE)], cwd=ROOT, capture_output=True, text=True
        )
        assert done.returncode == 0, done.stdout


class TestComfyUI:
    def _tree(self, base: Path) -> Path:
        touch(base, "main.py", "folder_paths.py", "comfy/", "comfy_extras/", "custom_nodes/")
        _files(base / "models", "checkpoints", "diffusion_models", "unet", "text_encoders", "clip", "loras", "vae",
               "embeddings", "upscale_models", "latent_upscale_models", "controlnet", "t2i_adapter",
               "ultralytics/bbox", "ultralytics/segm")
        return base

    def test_install_dir(self, tmp_path, real_catalog):
        self._tree(tmp_path)
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"]["id"] == "comfyui" and result["profile"]["confidence"] == "strong"
        assert _pairs(result) == {
            ("checkpoint", "models/checkpoints"), ("diffusion_model", "models/diffusion_models"),
            ("diffusion_model", "models/unet"), ("text_encoder", "models/text_encoders"),
            ("text_encoder", "models/clip"), ("lora", "models/loras"), ("vae", "models/vae"),
            ("embedding", "models/embeddings"), ("upscaler", "models/upscale_models"),
            ("upscaler", "models/latent_upscale_models"), ("controlnet", "models/controlnet"),
            ("controlnet", "models/t2i_adapter"), ("detection_bbox", "models/ultralytics/bbox"),
            ("detection_segm", "models/ultralytics/segm"),
        }
        assert _sug(result, "models/unet")["scan_headers"] is True
        assert _sug(result, "models/diffusion_models")["write"] is True and _sug(result, "models/unet")["write"] is False

    def test_models_dir(self, tmp_path, real_catalog):
        self._tree(tmp_path)
        result = detect_layout(str(tmp_path / "models"), catalog=real_catalog)
        assert result["profile"]["id"] == "comfyui" and result["profile"]["install_path"] == str(tmp_path)
        assert ("diffusion_model", "unet") in _pairs(result) and ("detection_bbox", "ultralytics/bbox") in _pairs(result)

    def test_portable_wrapper(self, tmp_path, real_catalog):
        self._tree(tmp_path / "ComfyUI_windows_portable" / "ComfyUI")
        touch(tmp_path, "ComfyUI_windows_portable/python_embeded/python.exe")
        result = detect_layout(str(tmp_path / "ComfyUI_windows_portable"), catalog=real_catalog)
        assert result["profile"]["id"] == "comfyui"
        assert ("checkpoint", "ComfyUI/models/checkpoints") in _pairs(result)

    def test_extra_model_paths_yield_an_a1111_extra_root(self, tmp_path, real_catalog):
        install = self._tree(tmp_path / "comfy")
        other = tmp_path / "webui"
        _files(other, "models/Stable-diffusion", "models/Lora", "models/LyCORIS")
        (install / "extra_model_paths.yaml").write_text(
            f"a111:\n    base_path: {other.as_posix()}\n    checkpoints: models/Stable-diffusion\n"
            "    loras: |\n         models/Lora\n         models/LyCORIS\n", encoding="utf-8")
        result = detect_layout(str(install), catalog=real_catalog)
        extra = result["extra_roots"][0]
        assert Path(extra["path"]) == other / "models"
        assert {(s["model_type"], s["subdir"]) for s in extra["suggestions"]} == {
            ("checkpoint", "Stable-diffusion"), ("lora", "Lora"), ("lora", "LyCORIS")}


class TestA1111Family:
    def _webui(self, base: Path) -> Path:
        touch(base, "webui.py", "launch.py", "modules/paths_internal.py", "extensions-builtin/", "webui-user.bat", "webui-user.sh")
        _files(base, "models/Stable-diffusion", "models/VAE", "models/Lora", "models/LyCORIS", "models/ESRGAN",
               "models/RealESRGAN", "models/ControlNet", "models/adetailer", "embeddings")
        return base

    def test_a1111_install_dir(self, tmp_path, real_catalog):
        self._webui(tmp_path)
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"]["id"] == "a1111" and result["profile"]["variant"] == "AUTOMATIC1111"
        assert _pairs(result) == {
            ("checkpoint", "models/Stable-diffusion"), ("vae", "models/VAE"), ("lora", "models/Lora"),
            ("lora", "models/LyCORIS"), ("upscaler", "models/ESRGAN"), ("upscaler", "models/RealESRGAN"),
            ("controlnet", "models/ControlNet"), ("adetailer", "models/adetailer"), ("embedding", "embeddings"),
        }
        assert _sug(result, "models/Stable-diffusion")["scan_headers"] is True

    def test_a1111_models_dir_offers_the_install_folder_for_embeddings(self, tmp_path, real_catalog):
        self._webui(tmp_path)
        result = detect_layout(str(tmp_path / "models"), catalog=real_catalog)
        assert result["profile"]["id"] == "a1111"
        assert result["outside_folders"] == [{
            "model_type": "embedding", "path": str(tmp_path / "embeddings"), "label": "embeddings",
            "install_path": str(tmp_path)}]
        assert ("embedding", "embeddings") not in _pairs(result)

    def test_forge_adds_the_text_encoder_folder(self, tmp_path, real_catalog):
        self._webui(tmp_path)
        touch(tmp_path, "modules_forge/")
        _files(tmp_path, "models/text_encoder")
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"]["variant"] == "Forge"
        assert ("text_encoder", "models/text_encoder") in _pairs(result)

    def test_sdnext_keeps_embeddings_under_models_and_maps_unet(self, tmp_path, real_catalog):
        touch(tmp_path, "webui.py", "launch.py", "installer.py", "modules/ui_definitions.py", "extensions-builtin/")
        _files(tmp_path, "models/Stable-diffusion", "models/UNET", "models/Text-encoder", "models/Lora", "models/VAE",
               "models/embeddings")
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"]["id"] == "a1111" and result["profile"]["variant"] == "SD.Next"
        assert ("diffusion_model", "models/UNET") in _pairs(result)
        assert _sug(result, "models/UNET")["scan_headers"] is True
        assert ("text_encoder", "models/Text-encoder") in _pairs(result)
        assert ("embedding", "models/embeddings") in _pairs(result)
        assert result["outside_folders"] == []

    def test_sdnext_config_json_moves_the_models_dir(self, tmp_path, real_catalog):
        touch(tmp_path / "sdnext", "webui.py", "launch.py", "installer.py", "modules/ui_definitions.py", "extensions-builtin/")
        elsewhere = tmp_path / "big" / "models"
        _files(elsewhere, "Stable-diffusion", "Lora")
        (tmp_path / "sdnext" / "config.json").write_text(json.dumps({"models_dir": str(elsewhere)}), encoding="utf-8")
        result = detect_layout(str(tmp_path / "sdnext"), catalog=real_catalog)
        extra = result["extra_roots"][0]
        assert extra["primary"] is True and Path(extra["path"]) == elsewhere
        assert {s["subdir"] for s in extra["suggestions"]} == {"Stable-diffusion", "Lora"}

    def test_webui_user_args_move_the_checkpoint_folder(self, tmp_path, real_catalog):
        install = self._webui(tmp_path / "webui")
        shared = tmp_path / "shared" / "ckpt"
        _files(shared, ".")
        (install / "webui-user.sh").write_text(f'export COMMANDLINE_ARGS="--ckpt-dir {shared.as_posix()}"\n', encoding="utf-8")
        result = detect_layout(str(install), catalog=real_catalog)
        assert Path(result["extra_roots"][0]["path"]) == shared.parent


class TestStabilityMatrix:
    FOLDERS = ("StableDiffusion", "DiffusionModels", "TextEncoders", "Lora", "LyCORIS", "VAE", "Embeddings", "ESRGAN",
               "RealESRGAN", "ControlNet", "T2IAdapter", "AfterDetailer", "Ultralytics/bbox", "Ultralytics/segm")

    def _portable(self, base: Path) -> Path:
        touch(base, "Data/.sm-portable", "Data/Packages/", "StabilityMatrix.exe")
        _files(base / "Data" / "Models", *self.FOLDERS)
        return base

    def test_portable_app_dir(self, tmp_path, real_catalog):
        self._portable(tmp_path)
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"]["id"] == "stabilitymatrix" and result["profile"]["confidence"] == "strong"
        assert _pairs(result) == {
            ("checkpoint", "Data/Models/StableDiffusion"), ("diffusion_model", "Data/Models/DiffusionModels"),
            ("text_encoder", "Data/Models/TextEncoders"), ("lora", "Data/Models/Lora"), ("lora", "Data/Models/LyCORIS"),
            ("vae", "Data/Models/VAE"), ("embedding", "Data/Models/Embeddings"), ("upscaler", "Data/Models/ESRGAN"),
            ("upscaler", "Data/Models/RealESRGAN"), ("controlnet", "Data/Models/ControlNet"),
            ("controlnet", "Data/Models/T2IAdapter"), ("adetailer", "Data/Models/AfterDetailer"),
            ("detection_bbox", "Data/Models/Ultralytics/bbox"), ("detection_segm", "Data/Models/Ultralytics/segm"),
        }
        assert _sug(result, "Data/Models/StableDiffusion")["scan_headers"] is True
        assert _sug(result, "Data/Models/Lora")["write"] is True and _sug(result, "Data/Models/LyCORIS")["write"] is False

    def test_models_dir_is_the_suffix_anchor(self, tmp_path, real_catalog):
        self._portable(tmp_path)
        result = detect_layout(str(tmp_path / "Data" / "Models"), catalog=real_catalog)
        assert result["profile"]["id"] == "stabilitymatrix"
        assert {"StableDiffusion", "DiffusionModels", "TextEncoders", "Lora", "LyCORIS"} <= {s["subdir"] for s in result["suggestions"]}

    def test_data_dir(self, tmp_path, real_catalog):
        self._portable(tmp_path)
        result = detect_layout(str(tmp_path / "Data"), catalog=real_catalog)
        assert result["profile"]["id"] == "stabilitymatrix" and ("lora", "Models/Lora") in _pairs(result)

    def test_installed_library_without_the_portable_marker(self, tmp_path, real_catalog):
        touch(tmp_path, "Packages/", "settings.json")
        _files(tmp_path / "Models", "StableDiffusion", "Lora", "LyCORIS")
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"]["id"] == "stabilitymatrix"
        assert {("lora", "Models/Lora"), ("lora", "Models/LyCORIS")} <= _pairs(result)

    def test_legacy_folder_names_are_bound(self, tmp_path, real_catalog):
        touch(tmp_path, "Data/.sm-portable", "Data/Packages/")
        _files(tmp_path / "Data" / "Models", "StableDiffusion", "CLIP", "Unet", "TextualInversion")
        pairs = _pairs(detect_layout(str(tmp_path), catalog=real_catalog))
        assert {("text_encoder", "Data/Models/CLIP"), ("diffusion_model", "Data/Models/Unet"),
                ("embedding", "Data/Models/TextualInversion")} <= pairs

    def test_model_directory_override_becomes_a_primary_extra_root(self, tmp_path, real_catalog):
        touch(tmp_path / "sm", "Data/.sm-portable", "Data/Packages/")
        big = tmp_path / "big"
        _files(big, "StableDiffusion", "Lora", "LyCORIS")
        (tmp_path / "sm" / "Data" / "settings.json").write_text(json.dumps({"ModelDirectoryOverride": str(big)}), encoding="utf-8")
        result = detect_layout(str(tmp_path / "sm"), catalog=real_catalog)
        extra = result["extra_roots"][0]
        assert extra["primary"] is True and extra["profile_id"] == "stabilitymatrix" and Path(extra["path"]) == big
        assert {s["subdir"] for s in extra["suggestions"]} == {"StableDiffusion", "Lora", "LyCORIS"}


class TestFooocus:
    def _tree(self, base: Path) -> Path:
        touch(base, "entry_with_update.py", "fooocus_version.py", "launch.py")
        _files(base / "models", "checkpoints", "loras", "embeddings", "vae", "upscale_models", "controlnet")
        return base

    def test_install_dir_wins_over_the_comfy_like_folder_names(self, tmp_path, real_catalog):
        self._tree(tmp_path)
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"]["id"] == "fooocus"
        assert _pairs(result) == {
            ("checkpoint", "models/checkpoints"), ("lora", "models/loras"), ("embedding", "models/embeddings"),
            ("vae", "models/vae"), ("upscaler", "models/upscale_models"), ("controlnet", "models/controlnet")}

    def test_folder_names_alone_never_match_fooocus(self, tmp_path, real_catalog):
        _files(tmp_path / "models", "checkpoints", "loras", "embeddings", "vae", "upscale_models", "controlnet")
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"]["id"] != "fooocus"
        assert all(a["id"] != "fooocus" for a in result["alternatives"])

    def test_portable_wrapper_folder(self, tmp_path, real_catalog):
        self._tree(tmp_path / "Fooocus_win64" / "Fooocus")
        result = detect_layout(str(tmp_path / "Fooocus_win64"), catalog=real_catalog)
        assert result["profile"]["id"] == "fooocus"
        assert ("checkpoint", "Fooocus/models/checkpoints") in _pairs(result)

    def test_config_txt_path_outside_becomes_an_extra_root(self, tmp_path, real_catalog):
        install = self._tree(tmp_path / "fooocus")
        elsewhere = tmp_path / "shared" / "ckpts"
        _files(elsewhere, ".")
        (install / "config.txt").write_text(json.dumps({"path_checkpoints": str(elsewhere)}), encoding="utf-8")
        result = detect_layout(str(install), catalog=real_catalog)
        assert Path(result["extra_roots"][0]["path"]) == elsewhere.parent


class TestSwarmUI:
    def _tree(self, base: Path) -> Path:
        touch(base, "src/SwarmUI.csproj", "launch-windows.bat", "launch-linux.sh", "Data/Settings.fds")
        _files(base / "Models", "Stable-Diffusion", "diffusion_models", "unet", "Lora", "VAE", "Embeddings", "controlnet",
               "text_encoders", "clip", "upscale_models", "ESRGAN", "RealESRGAN", "yolov8")
        return base

    def test_install_dir_and_the_nested_comfy_is_ignored(self, tmp_path, real_catalog):
        self._tree(tmp_path)
        inner = tmp_path / "dlbackend" / "comfy" / "ComfyUI"
        touch(inner, "main.py", "folder_paths.py", "comfy/", "comfy_extras/")
        _files(inner / "models", "checkpoints", "loras")
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"]["id"] == "swarmui"
        assert _pairs(result) == {
            ("checkpoint", "Models/Stable-Diffusion"), ("diffusion_model", "Models/diffusion_models"),
            ("diffusion_model", "Models/unet"), ("lora", "Models/Lora"), ("vae", "Models/VAE"),
            ("embedding", "Models/Embeddings"), ("controlnet", "Models/controlnet"),
            ("text_encoder", "Models/text_encoders"), ("text_encoder", "Models/clip"),
            ("upscaler", "Models/upscale_models"), ("upscaler", "Models/ESRGAN"), ("upscaler", "Models/RealESRGAN"),
            ("adetailer", "Models/yolov8"),
        }
        assert _sug(result, "Models/Stable-Diffusion")["scan_headers"] is True
        assert _sug(result, "Models/unet")["scan_headers"] is True

    def test_models_dir_uses_the_suffix_anchor(self, tmp_path, real_catalog):
        self._tree(tmp_path)
        result = detect_layout(str(tmp_path / "Models"), catalog=real_catalog)
        assert result["profile"]["id"] == "swarmui" and ("checkpoint", "Stable-Diffusion") in _pairs(result)

    def test_settings_fds_extra_model_root(self, tmp_path, real_catalog):
        self._tree(tmp_path / "swarm")
        second = tmp_path / "second"
        _files(second, "Stable-Diffusion", "Lora")
        (tmp_path / "swarm" / "Data" / "Settings.fds").write_text(f"Paths:\n\tModelRoot: Models;{second}\n", encoding="utf-8")
        result = detect_layout(str(tmp_path / "swarm"), catalog=real_catalog)
        extra = result["extra_roots"][0]
        assert Path(extra["path"]) == second and extra["primary"] is False
        assert {s["subdir"] for s in extra["suggestions"]} == {"Stable-Diffusion", "Lora"}


class TestPinokio:
    def _home(self, base: Path) -> Path:
        touch(base, "api/", "drive/", "bin/", "ENVIRONMENT")
        comfy = base / "api" / "comfy.git"
        touch(comfy, "pinokio.js")
        touch(comfy, "app/main.py", "app/folder_paths.py", "app/comfy/", "app/comfy_extras/")
        _files(comfy / "app" / "models", "checkpoints", "loras", "vae")
        forge = base / "api" / "stable-diffusion-webui-forge.git"
        touch(forge, "pinokio.js")
        touch(forge, "app/webui.py", "app/launch.py", "app/modules_forge/", "app/modules/", "app/extensions-builtin/")
        _files(forge / "app" / "models", "Stable-diffusion", "Lora", "VAE")
        fooocus = base / "api" / "fooocus.pinokio"
        touch(fooocus, "install.json")
        touch(fooocus, "Fooocus/entry_with_update.py", "Fooocus/fooocus_version.py")
        _files(fooocus / "Fooocus" / "models", "checkpoints", "loras")
        return base

    def test_home_lists_every_installed_app(self, tmp_path, real_catalog):
        home = self._home(tmp_path)
        result = detect_layout(str(home), catalog=real_catalog)
        assert result["profile"]["id"] == "pinokio" and result["path"] == str(home)
        found = {(Path(d["path"]).parts[-2 if Path(d["path"]).name == "app" else -1], d["profile"]["id"]) for d in result["delegated"]}
        assert found == {
            ("comfy.git", "comfyui"), ("stable-diffusion-webui-forge.git", "a1111"), ("Fooocus", "fooocus")}
        assert result["root_path"] == result["delegated"][0]["path"] and result["suggestions"]

    def test_launcher_dir_with_pinokio_js_delegates_to_its_app(self, tmp_path, real_catalog):
        home = self._home(tmp_path)
        result = detect_layout(str(home / "api" / "comfy.git"), catalog=real_catalog)
        assert result["profile"]["id"] == "pinokio"
        assert [d["profile"]["id"] for d in result["delegated"]] == ["comfyui"]
        assert ("checkpoint", "models/checkpoints") in _pairs(result)

    def test_app_dir_is_detected_as_the_tool(self, tmp_path, real_catalog):
        home = self._home(tmp_path)
        result = detect_layout(str(home / "api" / "stable-diffusion-webui-forge.git" / "app"), catalog=real_catalog)
        assert result["profile"]["id"] == "a1111" and result["profile"]["variant"] == "Forge"

    def test_a_project_with_an_api_folder_is_not_pinokio(self, tmp_path, real_catalog):
        touch(tmp_path, "api/routes.py", "README.md")
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"] is None

    def test_linked_drive_folders_do_not_duplicate_apps(self, tmp_path, real_catalog):
        import os

        home = self._home(tmp_path)
        real = home / "drive" / "shared_comfy"
        _files(real / "models", "checkpoints")
        touch(real, "main.py", "folder_paths.py", "comfy/", "comfy_extras/")
        try:
            os.symlink(real, home / "api" / "comfy2.git", target_is_directory=True)
            os.symlink(real, home / "api" / "comfy3.git", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks unavailable")
        result = detect_layout(str(home), catalog=real_catalog)
        assert len([d for d in result["delegated"] if d["profile"]["id"] == "comfyui"]) == 2


class TestGenericFallback:
    def test_unknown_tree_stays_generic(self, tmp_path, real_catalog):
        _files(tmp_path, "stable-diffusion", "Lora")
        result = detect_layout(str(tmp_path), catalog=real_catalog)
        assert result["profile"] is None
        assert {(s["model_type"], s["subdir"]) for s in result["suggestions"]} == {
            ("checkpoint", "stable-diffusion"), ("lora", "Lora")}
