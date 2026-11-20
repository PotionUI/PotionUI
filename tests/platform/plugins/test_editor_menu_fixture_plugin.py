import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.plugins.operations.frontend import get_frontend_extensions
from src.platform.plugins.loader import EXTRA_PLUGIN_DIRS_ENV
from src.platform.plugins.registry import PluginRegistry

TESTS = Path(__file__).resolve().parents[2]
SLOTS_ROOT = TESTS / "e2e" / "effect_plugins"
SLOTS_DIST = SLOTS_ROOT / "e2e-editor-slots" / "frontend" / "dist"
PLUGIN = "e2e-editor-slots"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv(EXTRA_PLUGIN_DIRS_ENV, str(SLOTS_ROOT))
    marketplace, local = tmp_path / "marketplace", tmp_path / "local"
    marketplace.mkdir()
    local.mkdir()
    tasks = register_core_tasks(TaskRegistry())
    registry = PluginRegistry(marketplace_dir=str(marketplace), local_dir=str(local), editor_task_registry=tasks)
    repo = SimpleNamespace(get_enabled_plugins=lambda: [SimpleNamespace(id=m.id) for m in registry.get_enabled_plugins()])
    return SimpleNamespace(registry=registry, repo=repo)


def test_the_manifest_declares_a_tools_item_and_a_plugin_menu_item(world):
    manifest = world.registry.get_plugin(PLUGIN)

    assert manifest.validation_error is None
    assert {e["id"]: e["menu"] for e in manifest.editor_menu} == {
        f"{PLUGIN}.note": "tools",
        f"{PLUGIN}.shelf": f"{PLUGIN}.shelf",
    }


def test_every_menu_module_is_in_the_built_bundle(world):
    manifest = world.registry.get_plugin(PLUGIN)

    assert manifest.editor_menu and all((SLOTS_DIST / e["module"]).is_file() for e in manifest.editor_menu)


def test_the_extension_list_carries_the_menu_items_with_dist_relative_modules(world):
    assert world.registry.enable_plugin(PLUGIN) is True

    listed = get_frontend_extensions(world.repo, world.registry)["editor_menu"]

    by_id = {e["id"]: e for e in listed}
    assert by_id[f"{PLUGIN}.note"]["plugin_id"] == PLUGIN
    assert by_id[f"{PLUGIN}.note"]["module"] == "editor/menuNote.js"
    assert by_id[f"{PLUGIN}.note"]["group"] == f"{PLUGIN}.stylize"
    assert by_id[f"{PLUGIN}.shelf"]["menu_label"] == "Shelf"


def test_disabling_the_plugin_removes_its_menu_items(world):
    world.registry.enable_plugin(PLUGIN)

    assert world.registry.disable_plugin(PLUGIN) is True

    assert get_frontend_extensions(world.repo, world.registry)["editor_menu"] == []
