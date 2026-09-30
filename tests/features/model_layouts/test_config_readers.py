import json
import ntpath
import posixpath
from pathlib import Path

import pytest

from src.features.model_layouts.readers.base import MAX_CONFIG_BYTES
from src.features.model_layouts.readers.registry import READERS, reader_kinds, run_reader
from src.features.model_layouts.readers.swarmui import parse_fds
from src.features.model_layouts.readers.webui_args import extract_commandline_args, parse_args, tokenize
from src.features.model_layouts.schema import READER_KINDS
from src.features.model_layouts.wsl import PathTranslator

POSIX = PathTranslator(is_windows=False, mount_root="/wsl", is_dir=lambda p: p in {"/wsl/d"})
WINDOWS = PathTranslator(is_windows=True, is_dir=lambda p: False)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _under(base: Path, *parts: str) -> str:
    return posixpath.normpath(posixpath.join(str(base), *parts))


def test_registry_matches_schema_reader_kinds():
    assert set(reader_kinds()) == READER_KINDS == set(READERS)


def test_unknown_kind_warns_and_never_raises(tmp_path):
    result = run_reader("nope", tmp_path)
    assert result.entries == [] and "Unknown config reader" in result.warnings[0]


@pytest.mark.parametrize("kind", sorted(READER_KINDS))
def test_every_reader_returns_nothing_for_an_empty_dir(kind, tmp_path):
    result = run_reader(kind, tmp_path, root=tmp_path)
    assert result.paths == {} and result.warnings == [] and result.primary_root is None
    assert result.outside_root is False


@pytest.mark.parametrize("kind,name", [
    ("comfyui_extra_model_paths", "extra_model_paths.yaml"),
    ("fooocus_config_txt", "config.txt"),
    ("swarmui_settings_fds", "Data/Settings.fds"),
    ("stabilitymatrix_settings_json", "settings.json"),
    ("sdnext_config_json", "config.json"),
    ("a1111_commandline_args", "webui-user.sh"),
])
class TestDefensive:
    def test_binary_garbage_never_raises(self, kind, name, tmp_path):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(bytes(range(256)) * 8)
        result = run_reader(kind, tmp_path, root=tmp_path)
        assert isinstance(result.warnings, list)

    def test_oversized_file_is_skipped_with_warning(self, kind, name, tmp_path):
        _write(tmp_path / name, "a" * (MAX_CONFIG_BYTES + 10))
        result = run_reader(kind, tmp_path, root=tmp_path)
        assert result.entries == [] and result.primary_root is None
        assert any("limit" in w for w in result.warnings)

    def test_directory_in_place_of_file_is_ignored(self, kind, name, tmp_path):
        (tmp_path / name).mkdir(parents=True)
        assert run_reader(kind, tmp_path).paths == {}

    def test_empty_file_yields_nothing(self, kind, name, tmp_path):
        _write(tmp_path / name, "")
        assert run_reader(kind, tmp_path).paths == {}


COMFY_YAML = "\n".join([
    'comfyui:',
    '    base_path: /data/comfy/',
    '    is_default: true',
    '    checkpoints: models/checkpoints/',
    '    text_encoders: |',
    '         models/text_encoders/',
    '         models/clip/',
    '    diffusion_models: |',
    '         models/unet/',
    '         models/diffusion_models/',
    '    controlnet: |',
    '         models/controlnet/',
    '         models/t2i_adapter/',
    '    clip_vision: models/clip_vision/',
    '    custom_nodes: custom_nodes/',
    'a111:',
    '    base_path: /data/a1111',
    '    checkpoints: models/Stable-diffusion',
    '    loras: |',
    '         models/Lora',
    '         models/LyCORIS',
    '    upscale_models: |',
    '                  models/ESRGAN',
    '                  models/RealESRGAN',
    '    embeddings: embeddings',
    'stability_matrix:',
    '    checkpoints: /data/sm/Models/StableDiffusion',
    '    vae: /data/sm/Models/VAE',
    'other_ui:',
]) + "\n"


class TestComfy:
    def _run(self, tmp_path, text=COMFY_YAML, root=None, translator=POSIX):
        _write(tmp_path / "extra_model_paths.yaml", text)
        return run_reader("comfyui_extra_model_paths", tmp_path, root=root, translator=translator)

    def test_paths_grouped_by_model_type_with_legacy_keys(self, tmp_path):
        paths = self._run(tmp_path).paths
        assert paths["checkpoint"] == [
            "/data/comfy/models/checkpoints",
            "/data/a1111/models/Stable-diffusion",
            "/data/sm/Models/StableDiffusion",
        ]
        assert paths["text_encoder"] == ["/data/comfy/models/text_encoders", "/data/comfy/models/clip"]
        assert paths["diffusion_model"] == ["/data/comfy/models/unet", "/data/comfy/models/diffusion_models"]
        assert paths["controlnet"] == ["/data/comfy/models/controlnet", "/data/comfy/models/t2i_adapter"]
        assert paths["lora"] == ["/data/a1111/models/Lora", "/data/a1111/models/LyCORIS"]
        assert paths["upscaler"] == ["/data/a1111/models/ESRGAN", "/data/a1111/models/RealESRGAN"]
        assert paths["embedding"] == ["/data/a1111/embeddings"]
        assert paths["vae"] == ["/data/sm/Models/VAE"]

    def test_unmapped_keys_are_ignored_silently(self, tmp_path):
        result = self._run(tmp_path)
        assert result.warnings == []
        assert all("clip_vision" not in p and "custom_nodes" not in p for v in result.paths.values() for p in v)

    def test_sections_and_default_flag(self, tmp_path):
        entries = self._run(tmp_path).entries
        assert {e.section for e in entries} == {"comfyui", "a111", "stability_matrix"}
        assert all(e.is_default for e in entries if e.section == "comfyui")
        assert not any(e.is_default for e in entries if e.section == "a111")

    def test_relative_paths_resolve_against_the_yaml_dir(self, tmp_path):
        result = self._run(tmp_path, "sm:\n    loras: rel/Lora\n")
        assert result.paths == {"lora": [_under(tmp_path, "rel", "Lora")]}

    def test_outside_root_flag(self, tmp_path):
        text = COMFY_YAML + "inside:\n    base_path: /srv/install\n    loras: loras\n"
        result = self._run(tmp_path, text, root="/srv/install")
        assert result.outside_root is True
        flags = {e.path: e.outside_root for e in result.entries}
        assert flags["/srv/install/loras"] is False
        assert flags["/data/comfy/models/checkpoints"] is True

    def test_all_inside_root_is_not_outside(self, tmp_path):
        result = self._run(tmp_path, "s:\n    base_path: /srv/install\n    loras: loras\n", root="/srv/install")
        assert result.outside_root is False

    def test_root_none_never_flags(self, tmp_path):
        assert self._run(tmp_path).outside_root is False

    def test_relative_base_path_resolves_against_yaml_dir(self, tmp_path):
        result = self._run(tmp_path, "c:\n    base_path: ../shared\n    loras: loras\n")
        assert result.paths["lora"] == [_under(tmp_path, "..", "shared", "loras")]

    def test_file_name_is_matched_case_insensitively(self, tmp_path):
        _write(tmp_path / "Extra_Model_Paths.yaml", "c:\n    base_path: /x\n    vae: v\n")
        assert run_reader("comfyui_extra_model_paths", tmp_path, translator=POSIX).paths == {"vae": ["/x/v"]}

    def test_malformed_yaml_warns(self, tmp_path):
        result = self._run(tmp_path, "a: [unclosed")
        assert result.paths == {} and "not valid YAML" in result.warnings[0]

    def test_non_mapping_top_level_warns(self, tmp_path):
        result = self._run(tmp_path, "- a\n- b\n")
        assert result.paths == {} and "mapping of sections" in result.warnings[0]

    def test_non_string_value_warns_and_continues(self, tmp_path):
        result = self._run(tmp_path, "c:\n    base_path: /x\n    loras: 5\n    vae: v\n")
        assert result.paths == {"vae": ["/x/v"]}
        assert "c.loras" in result.warnings[0]

    def test_list_value_is_accepted(self, tmp_path):
        result = self._run(tmp_path, "c:\n    base_path: /x\n    loras: [a, b]\n")
        assert result.paths == {"lora": ["/x/a", "/x/b"]}

    def test_env_var_in_base_path_skips_the_section(self, tmp_path):
        result = self._run(tmp_path, "c:\n    base_path: $HOME/x\n    loras: l\nd:\n    base_path: /y\n    vae: v\n")
        assert result.paths == {"vae": ["/y/v"]}
        assert "environment variable" in result.warnings[0]

    def test_windows_paths_are_translated_through_the_mount(self, tmp_path):
        result = self._run(tmp_path, "s:\n    base_path: 'D:\\AI\\comfy'\n    loras: 'models\\loras'\n")
        assert result.paths == {"lora": ["/wsl/d/AI/comfy/models/loras"]}

    def test_unmounted_drive_warns(self, tmp_path):
        result = self._run(tmp_path, "s:\n    base_path: 'E:\\AI'\n    loras: l\n")
        assert result.paths == {} and "not mounted" in result.warnings[0]

    def test_windows_host_joins_with_backslashes(self, tmp_path):
        result = self._run(tmp_path, "s:\n    base_path: 'D:\\AI\\comfy'\n    loras: models/loras\n", translator=WINDOWS)
        assert result.paths == {"lora": [ntpath.normpath("D:\\AI\\comfy\\models\\loras")]}

    def test_legacy_unet_and_clip_keys_map_to_current_types(self, tmp_path):
        result = self._run(tmp_path, "c:\n    base_path: /x\n    unet: models/unet\n    clip: models/clip\n    t2i_adapter: models/t2i\n")
        assert result.paths == {
            "diffusion_model": ["/x/models/unet"],
            "text_encoder": ["/x/models/clip"],
            "controlnet": ["/x/models/t2i"],
        }

    def test_bom_is_tolerated(self, tmp_path):
        (tmp_path / "extra_model_paths.yaml").write_bytes(b"\xef\xbb\xbfc:\n    base_path: /x\n    vae: v\n")
        assert run_reader("comfyui_extra_model_paths", tmp_path, translator=POSIX).paths == {"vae": ["/x/v"]}


class TestFooocus:
    def test_string_and_list_values_resolve_against_the_install_dir(self, tmp_path):
        for name in ("cp1", "cp2", "loras", "emb", "vae"):
            (tmp_path / name).mkdir()
        _write(tmp_path / "config.txt", json.dumps({
            "path_checkpoints": ["cp1", "cp2"],
            "path_loras": "loras",
            "path_embeddings": str(tmp_path / "emb"),
            "path_vae": "vae",
            "default_model": "x.safetensors",
        }))
        result = run_reader("fooocus_config_txt", tmp_path, root=tmp_path)
        assert result.paths == {
            "checkpoint": [str(tmp_path / "cp1"), str(tmp_path / "cp2")],
            "lora": [str(tmp_path / "loras")],
            "embedding": [str(tmp_path / "emb")],
            "vae": [str(tmp_path / "vae")],
        }
        assert result.outside_root is False
        assert any("environment variables" in w for w in result.warnings)

    def test_missing_folder_falls_back_with_warning(self, tmp_path):
        (tmp_path / "ok").mkdir()
        _write(tmp_path / "config.txt", json.dumps({"path_loras": "nope", "path_vae": ["ok", "nope"]}))
        result = run_reader("fooocus_config_txt", tmp_path)
        assert result.paths == {}
        assert sum("falls back to its default" in w for w in result.warnings) == 2

    def test_absolute_path_outside_root_is_flagged(self, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        root = tmp_path / "fooocus"
        _write(root / "config.txt", json.dumps({"path_checkpoints": str(elsewhere)}))
        result = run_reader("fooocus_config_txt", root, root=root)
        assert result.paths == {"checkpoint": [str(elsewhere)]} and result.outside_root is True

    def test_wrong_value_type_warns(self, tmp_path):
        _write(tmp_path / "config.txt", json.dumps({"path_loras": 5, "path_vae": [1]}))
        result = run_reader("fooocus_config_txt", tmp_path)
        assert result.paths == {} and len(result.warnings) == 2

    def test_malformed_json_warns(self, tmp_path):
        _write(tmp_path / "config.txt", "{not json")
        result = run_reader("fooocus_config_txt", tmp_path)
        assert result.paths == {} and "not valid JSON" in result.warnings[0]

    def test_json_array_warns(self, tmp_path):
        _write(tmp_path / "config.txt", "[1, 2]")
        assert "JSON object" in run_reader("fooocus_config_txt", tmp_path).warnings[0]

    def test_deeply_nested_json_does_not_raise(self, tmp_path):
        _write(tmp_path / "config.txt", "[" * 50000)
        result = run_reader("fooocus_config_txt", tmp_path)
        assert result.paths == {} and "not valid JSON" in result.warnings[0]

    def test_invalid_utf8_is_replaced_not_raised(self, tmp_path):
        (tmp_path / "config.txt").write_bytes(b'{"path_loras": "\xff\xfe"}')
        result = run_reader("fooocus_config_txt", tmp_path)
        assert result.paths == {} and any("falls back" in w for w in result.warnings)

    def test_windows_drive_path_uses_the_injected_mount(self, tmp_path):
        mount_root = tmp_path / "mnt"
        (mount_root / "d" / "Fooocus" / "checkpoints").mkdir(parents=True)
        _write(tmp_path / "config.txt", json.dumps({"path_checkpoints": "D:\\Fooocus\\checkpoints"}))
        translator = PathTranslator(is_windows=False, mount_root=str(mount_root))
        result = run_reader("fooocus_config_txt", tmp_path, translator=translator)
        assert [Path(p) for p in result.paths["checkpoint"]] == [mount_root / "d" / "Fooocus" / "checkpoints"]


SWARM_FDS = "\n".join([
    '# Swarm settings',
    'Paths:',
    '{i}ModelRoot: Models;/mnt/data/extra/models',
    '{i}SDModelFolder: Stable-Diffusion;checkpoints',
    '{i}SDLoraFolder: Lora',
    '{i}SDVAEFolder: /abs/vae',
    '{i}SDEmbeddingFolder: Embeddings',
    '{i}SDControlNetsFolder: controlnet;model_patches',
    '{i}SDClipFolder: text_encoders;clip',
    '{i}SDClipVisionFolder: clip_vision',
    '{i}DataPath: Data',
    'Server:',
    '{i}Port: 7801',
]) + "\n"


class TestSwarm:
    @pytest.mark.parametrize("indent", ["    ", "\t", "  "])
    def test_tabs_or_spaces(self, tmp_path, indent):
        _write(tmp_path / "Data" / "Settings.fds", SWARM_FDS.format(i=indent))
        result = run_reader("swarmui_settings_fds", tmp_path, root=tmp_path, translator=POSIX)
        models = _under(tmp_path, "Models")
        assert result.primary_root == models and result.primary_outside_root is False
        assert result.extra_roots == ["/mnt/data/extra/models"]
        assert result.paths == {
            "checkpoint": [f"{models}/Stable-Diffusion", f"{models}/checkpoints"],
            "lora": [f"{models}/Lora"],
            "vae": ["/abs/vae"],
            "embedding": [f"{models}/Embeddings"],
            "controlnet": [f"{models}/controlnet", f"{models}/model_patches"],
            "text_encoder": [f"{models}/text_encoders", f"{models}/clip"],
        }
        assert result.outside_root is True

    def test_only_model_root_gives_primary_without_entries(self, tmp_path):
        _write(tmp_path / "Data" / "Settings.fds", "Paths:\n\tModelRoot: /mnt/data/models\n")
        result = run_reader("swarmui_settings_fds", tmp_path, root=str(tmp_path), translator=POSIX)
        assert result.primary_root == "/mnt/data/models" and result.paths == {}
        assert result.primary_outside_root is True

    def test_keys_are_case_insensitive_and_only_under_paths(self, tmp_path):
        _write(tmp_path / "data" / "settings.fds", "Other:\n\tModelRoot: /no\npaths:\n\tmodelroot: /yes\n")
        assert run_reader("swarmui_settings_fds", tmp_path, translator=POSIX).primary_root == "/yes"

    def test_absent_model_root_yields_nothing(self, tmp_path):
        _write(tmp_path / "Data" / "Settings.fds", "Paths:\n\tSDLoraFolder: Lora\n")
        assert run_reader("swarmui_settings_fds", tmp_path).paths == {}

    def test_windows_model_root_is_translated(self, tmp_path):
        _write(tmp_path / "Data" / "Settings.fds", "Paths:\n\tModelRoot: D:\\Swarm\\Models\n")
        result = run_reader("swarmui_settings_fds", tmp_path, translator=POSIX)
        assert result.primary_root == "/wsl/d/Swarm/Models"

    def test_parse_fds_ignores_comments_blank_and_colonless_lines(self):
        parsed = parse_fds("# c\n\nA:\n  # c2\n  b: 1\n  junk\n  c:\n    d: x: y\ne: 2\n")
        assert parsed == [(("a", "b"), "1"), (("a", "c", "d"), "x: y"), (("e",), "2")]


class TestStabilityMatrix:
    def _run(self, tmp_path, **kwargs):
        return run_reader("stabilitymatrix_settings_json", tmp_path, translator=POSIX, **kwargs)

    def test_override_in_portable_data_dir(self, tmp_path):
        _write(tmp_path / "Data" / "settings.json", json.dumps({"ModelDirectoryOverride": "/mnt/data/models"}))
        result = self._run(tmp_path, root="/lib/Data/Models")
        assert result.primary_root == "/mnt/data/models" and result.primary_outside_root is True

    def test_key_case_is_ignored(self, tmp_path):
        _write(tmp_path / "settings.json", json.dumps({"modeldirectoryoverride": "/mnt/data/m"}))
        assert self._run(tmp_path).primary_root == "/mnt/data/m"

    def test_relative_override_resolves_against_the_library(self, tmp_path):
        _write(tmp_path / "settings.json", json.dumps({"ModelDirectoryOverride": "mine"}))
        assert self._run(tmp_path).primary_root == _under(tmp_path, "mine")

    def test_windows_override_is_translated(self, tmp_path):
        _write(tmp_path / "settings.json", json.dumps({"ModelDirectoryOverride": "D:\\Models"}))
        assert self._run(tmp_path).primary_root == "/wsl/d/Models"

    def test_no_override_reports_the_library_models_folder(self, tmp_path):
        _write(tmp_path / "settings.json", json.dumps({"Theme": "dark"}))
        (tmp_path / "Models").mkdir()
        result = run_reader("stabilitymatrix_settings_json", tmp_path, root=tmp_path)
        assert Path(result.primary_root) == tmp_path / "Models" and result.primary_outside_root is False

    def test_no_override_and_no_models_folder_reports_nothing(self, tmp_path):
        _write(tmp_path / "settings.json", "{}")
        assert self._run(tmp_path).primary_root is None

    def test_library_json_points_at_the_library_and_its_override(self, tmp_path):
        library = tmp_path / "lib"
        override = tmp_path / "ext" / "models"
        _write(library / "settings.json", json.dumps({"ModelDirectoryOverride": str(override)}))
        _write(tmp_path / "appdata" / "library.json", json.dumps({"LibraryPath": str(library)}))
        result = run_reader("stabilitymatrix_settings_json", tmp_path / "appdata")
        assert Path(result.primary_root) == override

    def test_library_json_without_settings_falls_back_to_models(self, tmp_path):
        library = tmp_path / "lib"
        (library / "Models").mkdir(parents=True)
        _write(tmp_path / "appdata" / "library.json", json.dumps({"LibraryPath": str(library)}))
        result = run_reader("stabilitymatrix_settings_json", tmp_path / "appdata")
        assert Path(result.primary_root) == library / "Models"

    def test_malformed_settings_warns(self, tmp_path):
        _write(tmp_path / "settings.json", "{oops")
        result = self._run(tmp_path)
        assert result.primary_root is None and "not valid JSON" in result.warnings[0]

    def test_non_string_override_is_ignored(self, tmp_path):
        _write(tmp_path / "settings.json", json.dumps({"ModelDirectoryOverride": 5}))
        assert self._run(tmp_path).primary_root is None


class TestSdNext:
    def test_dirs_map_to_types_and_relative_paths_use_the_install_dir(self, tmp_path):
        _write(tmp_path / "config.json", json.dumps({
            "models_dir": "/mnt/data/models",
            "ckpt_dir": "/mnt/data/models/Stable-diffusion",
            "diffusers_dir": "/mnt/data/models/Diffusers",
            "vae_dir": "vae",
            "unet_dir": "/mnt/data/models/UNET",
            "te_dir": "/mnt/data/models/Text-encoder",
            "lora_dir": "/mnt/data/models/Lora",
            "embeddings_dir": "/mnt/data/models/embeddings",
            "esrgan_models_path": "/mnt/data/models/ESRGAN",
            "realesrgan_models_path": "/mnt/data/models/RealESRGAN",
            "control_dir": "/mnt/data/models/control",
            "theme": "dark",
        }))
        result = run_reader("sdnext_config_json", tmp_path, root="/srv/sdnext", translator=POSIX)
        assert result.primary_root == "/mnt/data/models"
        assert result.paths == {
            "checkpoint": ["/mnt/data/models/Stable-diffusion"],
            "vae": [_under(tmp_path, "vae")],
            "diffusion_model": ["/mnt/data/models/UNET"],
            "text_encoder": ["/mnt/data/models/Text-encoder"],
            "lora": ["/mnt/data/models/Lora"],
            "embedding": ["/mnt/data/models/embeddings"],
            "upscaler": ["/mnt/data/models/ESRGAN", "/mnt/data/models/RealESRGAN"],
        }
        assert result.outside_root is True

    def test_non_string_values_are_ignored(self, tmp_path):
        _write(tmp_path / "config.json", json.dumps({"ckpt_dir": 5, "lora_dir": "", "models_dir": None}))
        result = run_reader("sdnext_config_json", tmp_path)
        assert result.paths == {} and result.primary_root is None

    def test_malformed_json_warns(self, tmp_path):
        _write(tmp_path / "config.json", "{")
        assert "not valid JSON" in run_reader("sdnext_config_json", tmp_path).warnings[0]

    def test_windows_paths_are_translated(self, tmp_path):
        _write(tmp_path / "config.json", json.dumps({"lora_dir": "D:\\sd\\lora"}))
        assert run_reader("sdnext_config_json", tmp_path, translator=POSIX).paths == {"lora": ["/wsl/d/sd/lora"]}


SH_FILE = "\n".join([
    '#!/bin/bash',
    '#export COMMANDLINE_ARGS="--ckpt-dir /commented"',
    'export COMMANDLINE_ARGS="--xformers --ckpt-dir /mnt/data/sd --lora-dir \'/mnt/data/lo ra\' --vae-dir=/mnt/data/vae"',
]) + "\n"

BAT_FILE = "\r\n".join([
    '@echo off',
    r'rem set COMMANDLINE_ARGS=--ckpt-dir C:\commented',
    'set PYTHON=',
    r'set COMMANDLINE_ARGS=--xformers --ckpt-dir "D:\AI Models\sd" --lora-dir D:\AI\lora',
    r'set COMMANDLINE_ARGS=%COMMANDLINE_ARGS% --embeddings-dir D:\AI\emb --text-encoder-dir "D:\AI\te"',
    'call webui.bat',
]) + "\r\n"


def _sh(tmp_path, args):
    _write(tmp_path / "webui-user.sh", f'export COMMANDLINE_ARGS="{args}"\n')
    return run_reader("a1111_commandline_args", tmp_path, root="/srv/webui", translator=POSIX)


class TestWebuiArgs:
    def test_sh_file(self, tmp_path):
        _write(tmp_path / "webui-user.sh", SH_FILE)
        result = run_reader("a1111_commandline_args", tmp_path, root="/srv/webui/models", translator=POSIX)
        assert result.paths == {
            "checkpoint": ["/mnt/data/sd"],
            "lora": ["/mnt/data/lo ra"],
            "vae": ["/mnt/data/vae"],
        }
        assert result.outside_root is True
        assert result.primary_root is None

    def test_bat_file_with_single_backslashes_quotes_and_self_reference(self, tmp_path):
        _write(tmp_path / "webui-user.bat", BAT_FILE)
        result = run_reader("a1111_commandline_args", tmp_path, translator=POSIX)
        assert result.paths == {
            "checkpoint": ["/wsl/d/AI Models/sd"],
            "lora": ["/wsl/d/AI/lora"],
            "embedding": ["/wsl/d/AI/emb"],
            "text_encoder": ["/wsl/d/AI/te"],
        }

    def test_bat_and_sh_are_merged_and_file_can_pin_one(self, tmp_path):
        _write(tmp_path / "webui-user.sh", 'export COMMANDLINE_ARGS="--vae-dir /a/vae"\n')
        _write(tmp_path / "webui-user.bat", "set COMMANDLINE_ARGS=--lora-dir /b/lora\n")
        both = run_reader("a1111_commandline_args", tmp_path, translator=POSIX)
        assert both.paths == {"vae": ["/a/vae"], "lora": ["/b/lora"]}
        pinned = run_reader("a1111_commandline_args", tmp_path, file="webui-user.bat", translator=POSIX)
        assert pinned.paths == {"lora": ["/b/lora"]}

    def test_models_dir_and_data_dir(self, tmp_path):
        result = _sh(tmp_path, "--data-dir /srv/data --models-dir /srv/m --esrgan-models-path /srv/m/ESRGAN")
        assert result.primary_root == "/srv/m" and result.primary_outside_root is True
        assert result.paths == {"upscaler": ["/srv/m/ESRGAN"], "embedding": ["/srv/data/embeddings"]}

    def test_data_dir_alone_implies_models_and_embeddings(self, tmp_path):
        _write(tmp_path / "webui-user.sh", "COMMANDLINE_ARGS=--data-dir=/srv/data\n")
        result = run_reader("a1111_commandline_args", tmp_path, translator=POSIX)
        assert result.primary_root == "/srv/data/models"
        assert result.paths == {"embedding": ["/srv/data/embeddings"]}

    def test_explicit_embeddings_dir_beats_the_data_dir_default(self, tmp_path):
        assert _sh(tmp_path, "--data-dir /d --embeddings-dir /e").paths == {"embedding": ["/e"]}

    def test_relative_dirs_resolve_against_the_install_dir(self, tmp_path):
        assert _sh(tmp_path, "--ckpt-dir shared/sd").paths == {"checkpoint": [_under(tmp_path, "shared", "sd")]}

    def test_env_var_value_is_skipped_with_warning(self, tmp_path):
        result = _sh(tmp_path, "--ckpt-dir $HOME/sd --vae-dir /v")
        assert result.paths == {"vae": ["/v"]} and "environment variable" in result.warnings[0]

    def test_bat_directory_variable_is_skipped_with_warning(self, tmp_path):
        _write(tmp_path / "webui-user.bat", r"set COMMANDLINE_ARGS=--ckpt-dir %~dp0models --vae-dir D:\v" + "\r\n")
        result = run_reader("a1111_commandline_args", tmp_path, translator=POSIX)
        assert result.paths == {"vae": ["/wsl/d/v"]} and "environment variable" in result.warnings[0]

    def test_command_substitution_is_skipped_with_warning(self, tmp_path):
        result = _sh(tmp_path, "--ckpt-dir $(pwd)/models --lora-dir /l")
        assert result.paths == {"lora": ["/l"]} and "environment variable" in result.warnings[0]

    def test_no_commandline_args_line_yields_nothing(self, tmp_path):
        _write(tmp_path / "webui-user.sh", '#export COMMANDLINE_ARGS="--ckpt-dir /x"\nexport FOO=1\n')
        assert run_reader("a1111_commandline_args", tmp_path).paths == {}

    def test_flag_without_value_is_ignored(self, tmp_path):
        assert _sh(tmp_path, "--ckpt-dir --vae-dir /v").paths == {"vae": ["/v"]}

    def test_unterminated_quote_does_not_raise(self, tmp_path):
        _write(tmp_path / "webui-user.sh", 'export COMMANDLINE_ARGS="--ckpt-dir /a --vae-dir "/b\n')
        assert isinstance(run_reader("a1111_commandline_args", tmp_path).warnings, list)


def test_tokenize_posix_and_bat_quoting():
    assert tokenize('--a "x y" \'p q\' z\\ w', posix=True) == ["--a", "x y", "p q", "z w"]
    assert tokenize('--a "C:\\My Dir\\x" \'lit\'', posix=False) == ["--a", "C:\\My Dir\\x", "'lit'"]
    assert tokenize('"" a', posix=True) == ["", "a"]
    assert tokenize("", posix=True) == []


def test_parse_args_forms():
    assert parse_args(["--a=1", "--b", "2", "--flag", "--c", "--d", "4", "pos"]) == {"--a": "1", "--b": "2", "--d": "4"}


def test_extract_commandline_args_last_assignment_wins():
    assert extract_commandline_args('COMMANDLINE_ARGS="--a"\nexport COMMANDLINE_ARGS="$COMMANDLINE_ARGS --b"\n', bat=False) == "--a --b"
    assert extract_commandline_args('set "COMMANDLINE_ARGS=--a"\n', bat=True) == "--a"
    assert extract_commandline_args("REM set COMMANDLINE_ARGS=--x\n:: set COMMANDLINE_ARGS=--y\n", bat=True) is None


def test_reader_exception_is_contained_and_partial_output_dropped(tmp_path, monkeypatch):
    def broken(ctx, file, result):
        ctx.add_entry(result, "lora", "/x", "k")
        ctx.set_primary(result, "/x")
        raise RuntimeError("boom")

    monkeypatch.setitem(READERS, "comfyui_extra_model_paths", broken)
    result = run_reader("comfyui_extra_model_paths", tmp_path)
    assert result.entries == [] and result.primary_root is None
    assert "boom" in result.warnings[0]
