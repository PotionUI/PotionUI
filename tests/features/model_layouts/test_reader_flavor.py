import ast
import json
import ntpath
import pathlib
from pathlib import Path

import pytest

from src.features.model_layouts.readers.registry import run_reader
from src.features.model_layouts.wsl import PathTranslator

READERS_DIR = Path(__file__).resolve().parents[3] / "src" / "features" / "model_layouts" / "readers"

POSIX = PathTranslator(is_windows=False, mount_root="/wsl", is_dir=lambda p: True)
WINDOWS = PathTranslator(is_windows=True, is_dir=lambda p: True)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _comfy(tmp: Path, style: dict):
    _write(tmp / "extra_model_paths.yaml", "\n".join([
        "a:",
        f"    base_path: {style['base']}",
        "    is_default: true",
        "    checkpoints: models/checkpoints",
        "    loras: |",
        "         models/loras",
        "         models/LyCORIS",
        "b:",
        f"    vae: {style['abs']}",
    ]) + "\n")
    return "comfyui_extra_model_paths", None


def _fooocus(tmp: Path, style: dict):
    _write(tmp / "config.txt", json.dumps({
        "path_checkpoints": [style["abs"] + "1", style["abs"] + "2"], "path_loras": style["abs"] + "3"}))
    return "fooocus_config_txt", None


def _swarm(tmp: Path, style: dict):
    _write(tmp / "Data" / "Settings.fds", "\n".join([
        "Paths:",
        f"\tModelRoot: {style['base']};{style['abs']}",
        "\tSDModelFolder: Stable-Diffusion;checkpoints",
        f"\tSDVAEFolder: {style['abs']}vae",
    ]) + "\n")
    return "swarmui_settings_fds", None


def _stabilitymatrix(tmp: Path, style: dict):
    _write(tmp / "settings.json", json.dumps({"ModelDirectoryOverride": style["abs"]}))
    return "stabilitymatrix_settings_json", None


def _sdnext(tmp: Path, style: dict):
    _write(tmp / "config.json", json.dumps({
        "models_dir": style["base"], "ckpt_dir": style["abs"] + "ckpt", "lora_dir": style["abs"] + "lora"}))
    return "sdnext_config_json", None


def _webui_sh(tmp: Path, style: dict):
    _write(tmp / "webui-user.sh", f'export COMMANDLINE_ARGS="--data-dir {style["base"]} --ckpt-dir {style["abs"]}ck"\n')
    return "a1111_commandline_args", "webui-user.sh"


SCENARIOS = [_comfy, _fooocus, _swarm, _stabilitymatrix, _sdnext, _webui_sh]
POSIX_STYLE = {"base": "/data/base", "abs": "/data/abs/"}
WINDOWS_STYLE = {"base": "D:\\\\data\\\\base", "abs": "D:\\\\data\\\\abs\\\\"}


def _summary(result):
    return {
        "paths": result.paths,
        "entries": [(e.model_type, e.path, e.key, e.outside_root, e.section, e.is_default) for e in result.entries],
        "primary_root": result.primary_root,
        "primary_outside_root": result.primary_outside_root,
        "extra_roots": result.extra_roots,
        "warnings": result.warnings,
    }


def _run(build, tmp_path, style, translator, root):
    kind, file = build(tmp_path, style)
    return run_reader(kind, tmp_path, root=root, file=file, translator=translator)


@pytest.mark.parametrize("build", SCENARIOS, ids=lambda b: b.__name__)
def test_posix_translator_output_ignores_how_the_host_prints_paths(build, tmp_path, monkeypatch):
    expected = _summary(_run(build, tmp_path, POSIX_STYLE, POSIX, "/data/base"))
    assert expected["entries"] or expected["primary_root"]
    with monkeypatch.context() as patched:
        original_str = pathlib.PurePath.__str__
        patched.setattr(pathlib.PurePath, "__str__", lambda self: original_str(self).replace("/", "\\"))
        patched.setattr(pathlib.PurePath, "__fspath__", original_str)
        actual = _summary(_run(build, tmp_path, POSIX_STYLE, POSIX, "/data/base"))
    assert actual == expected


@pytest.mark.parametrize("build", SCENARIOS, ids=lambda b: b.__name__)
def test_windows_translator_yields_windows_paths_on_any_host(build, tmp_path):
    result = _run(build, tmp_path, WINDOWS_STYLE, WINDOWS, "D:\\data\\base")
    strings = [e.path for e in result.entries] + [p for p in (result.primary_root,) if p] + result.extra_roots
    assert strings
    assert all(ntpath.isabs(s) and "/" not in s for s in strings), strings
    assert [w for w in result.warnings if "environment variables" not in w] == []


def test_windows_translator_outside_root_uses_windows_rules(tmp_path):
    result = _run(_comfy, tmp_path, WINDOWS_STYLE, WINDOWS, "D:\\DATA\\Base")
    flags = {e.path: e.outside_root for e in result.entries}
    assert flags["D:\\data\\base\\models\\checkpoints"] is False
    assert flags["D:\\data\\abs\\"[:-1]] is True


def test_windows_translator_joins_relative_folders_with_backslashes(tmp_path):
    result = _run(_swarm, tmp_path, WINDOWS_STYLE, WINDOWS, "D:\\data\\base")
    assert result.paths["checkpoint"] == ["D:\\data\\base\\Stable-Diffusion", "D:\\data\\base\\checkpoints"]
    assert result.primary_root == "D:\\data\\base"


def test_readers_do_not_use_the_host_path_layer_for_strings():
    offenders = []
    for path in sorted(READERS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                if node.attr != "fspath":
                    offenders.append(f"{path.name}:{node.lineno} os.{node.attr}")
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ("posixpath", "ntpath"):
                offenders.append(f"{path.name}:{node.lineno} {node.value.id}.{node.attr}")
    assert offenders == []
