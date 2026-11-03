from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.features.media_tools.service import effect_block
from src.platform.media_tools import MediaToolRegistry, ToolMedia, validate_params
from src.platform.media_tools.effects.runtime import EffectRuntime
from src.platform.media_tools.effects.tool import EffectTool
from src.platform.media_tools.ops import register_core_tools
from src.platform.plugins.registry import PluginRegistry, PluginState
from tests.platform.media_tools.effects.builder import effect_data, write_effect

FIXTURE_PLUGIN = Path(__file__).resolve().parents[2] / "e2e" / "effect_plugins" / "e2e-effects"


@pytest.fixture
def tools():
    registry = MediaToolRegistry()
    register_core_tools(registry)
    return registry


@pytest.fixture
def runtime():
    return EffectRuntime(filters=frozenset, torch_available=lambda: False)


@pytest.fixture
def plugins(tmp_path, tools, runtime):
    (tmp_path / "marketplace").mkdir()
    (tmp_path / "local").mkdir()
    return PluginRegistry(
        str(tmp_path / "marketplace"), str(tmp_path / "local"), media_tool_registry=tools, effect_runtime=runtime,
    )


def make_plugin(tmp_path, plugin_id="glow", roots=("effects",), effects=("dim",)) -> Path:
    folder = tmp_path / "marketplace" / plugin_id
    folder.mkdir(parents=True)
    manifest = {
        "id": plugin_id, "name": plugin_id, "version": "1.0.0", "description": "Test plugin",
        "author": "Test Author", "type": "backend-only", "effects": [{"path": root} for root in roots],
    }
    (folder / "manifest.yml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    for name in effects:
        write_effect(folder / roots[0], name)
    return folder


def test_enabling_registers_the_plugins_effects_under_its_own_source(tmp_path, plugins, tools, runtime):
    make_plugin(tmp_path, effects=("dim", "fog"))

    assert plugins.enable_plugin("glow")

    for tool_id in ("glow.dim", "glow.fog"):
        spec = tools.get(tool_id)
        assert spec is not None and spec.source == "glow" and spec.group == "effects"
        assert isinstance(spec.impl, EffectTool) and spec.impl.runtime is runtime
    block = effect_block(tools.get("glow.dim").impl.effect)
    assert block["shader"]["url"] == "/api/media-tools/effects/glow.dim/shader"
    assert block["group"] == "colour"


def test_a_plugin_effect_renders_through_its_own_python(tmp_path, plugins, tools):
    make_plugin(tmp_path)
    plugins.enable_plugin("glow")
    spec = tools.get("glow.dim")
    frames = np.full((1, 4, 4, 3), 200, dtype=np.uint8)

    media = ToolMedia(path=Path("clip.mp4"), kind="video", width=4, height=4, duration=1.0, fps=24.0)

    stage = spec.impl.video_filter(media, validate_params(spec, {"strength": 50})).stage

    assert np.all(stage.apply(frames, (0,), 24.0, 1.0) == 100)


def test_disabling_removes_only_that_plugins_effects_and_enabling_again_restores_them(tmp_path, plugins, tools):
    make_plugin(tmp_path, "glow")
    make_plugin(tmp_path, "haze", effects=("mist",))
    plugins.enable_plugin("glow")
    plugins.enable_plugin("haze")

    assert plugins.disable_plugin("glow")

    assert tools.get("glow.dim") is None and tools.get("haze.mist") is not None and tools.get("vignette") is not None
    assert plugins.enable_plugin("glow") and tools.get("glow.dim") is not None


def test_a_broken_effect_fails_the_plugin_and_registers_nothing(tmp_path, plugins, tools):
    folder = make_plugin(tmp_path, effects=("dim", "bad"))
    (folder / "effects" / "bad" / "effect.yml").write_text(yaml.safe_dump(effect_data(id="bad", render=[{"ffmpeg": {"vf": [{"drawtext": {}}]}}])), encoding="utf-8")

    assert not plugins.enable_plugin("glow")

    assert plugins.get_plugin_state("glow") == PluginState.ERROR
    assert tools.get("glow.dim") is None and tools.get("glow.bad") is None


def test_an_effects_root_must_be_a_folder_inside_the_plugin(tmp_path, plugins, tools):
    make_plugin(tmp_path, roots=("missing",), effects=())

    assert not plugins.enable_plugin("glow")
    assert plugins.get_plugin_state("glow") == PluginState.ERROR


def test_a_manifest_path_that_leaves_the_plugin_is_refused(tmp_path, plugins):
    folder = make_plugin(tmp_path, effects=())
    manifest = yaml.safe_load((folder / "manifest.yml").read_text())
    manifest["effects"] = [{"path": "../elsewhere"}]
    (folder / "manifest.yml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    assert not plugins.enable_plugin("glow")


def test_effects_need_the_media_tool_registry(tmp_path):
    (tmp_path / "marketplace").mkdir()
    (tmp_path / "local").mkdir()
    make_plugin(tmp_path)
    registry = PluginRegistry(str(tmp_path / "marketplace"), str(tmp_path / "local"))

    assert not registry.enable_plugin("glow")


def test_the_shipped_fixture_plugin_enables_and_adds_invert(tmp_path, plugins, tools):
    shutil.copytree(FIXTURE_PLUGIN, tmp_path / "marketplace" / "e2e-effects")

    assert plugins.enable_plugin("e2e-effects")

    spec = tools.get("e2e-effects.invert")
    assert spec.source == "e2e-effects" and spec.accepts == ("video", "image")
    assert spec.impl.effect.shader is not None
