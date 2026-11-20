import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.editor_tasks.routes import build_router
from src.features.plugins.operations.frontend import get_frontend_extensions
from src.platform.plugins.loader import EXTRA_PLUGIN_DIRS_ENV
from src.platform.plugins.registry import PluginRegistry
from src.platform.security import current_user
from src.platform.security.user import AccountType, User
from tests.platform.security.test_current_user_keys import FakeResolver

TESTS = Path(__file__).resolve().parents[2]
SLOTS_ROOT = TESTS / "e2e" / "effect_plugins"
SLOTS_DIST = SLOTS_ROOT / "e2e-editor-slots" / "frontend" / "dist"
OTHER_ROOT = TESTS / "fixtures" / "plugins"
PLUGIN = "e2e-editor-slots"
OTHER = "tool-tab-fixture"
SESSION = "a.session.jwt"
EDITOR_KEYS = ("editor_steps", "editor_tools", "editor_groups", "editor_overlays", "editor_tasks")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv(EXTRA_PLUGIN_DIRS_ENV, os.pathsep.join([str(SLOTS_ROOT), str(OTHER_ROOT)]))
    marketplace, local = tmp_path / "marketplace", tmp_path / "local"
    marketplace.mkdir()
    local.mkdir()
    tasks = register_core_tasks(TaskRegistry())
    registry = PluginRegistry(marketplace_dir=str(marketplace), local_dir=str(local), editor_task_registry=tasks)
    manager = EditorTaskManager(tasks, lambda: [], lambda user: set(), lambda engine: True)
    repo = SimpleNamespace(get_enabled_plugins=lambda: [SimpleNamespace(id=m.id) for m in registry.get_enabled_plugins()])
    return SimpleNamespace(registry=registry, tasks=tasks, manager=manager, repo=repo)


@pytest.fixture
def client(world):
    user = User(id="u1", username="u1", email="u1@example.com", password_hash="h", account_type=AccountType.USER)
    auth = Mock()
    auth.get_user_from_token.side_effect = lambda token: user if token == SESSION else None
    previous = (current_user._auth, current_user._key_resolver)
    current_user.set_auth(auth)
    current_user.set_key_resolver(FakeResolver())
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(editor_tasks=world.manager)))
    with TestClient(app) as test_client:
        yield test_client
    current_user._auth, current_user._key_resolver = previous


def listed_tasks(client):
    response = client.get("/api/editor/tasks?editor=image", headers={"Authorization": f"Bearer {SESSION}"})
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()["data"]["tasks"]}


def core_image_tasks(world):
    return {task.id for task in world.tasks.list() if "image" in task.editors}


def extensions(world):
    return get_frontend_extensions(world.repo, world.registry)


def test_the_manifest_loads_with_every_editor_part_validated(world):
    manifest = world.registry.get_plugin(PLUGIN)

    assert manifest.validation_error is None
    assert [e["id"] for e in manifest.editor_groups] == [f"{PLUGIN}.stylize"]
    assert [e["id"] for e in manifest.editor_steps] == [f"{PLUGIN}.tint", f"{PLUGIN}.tint-later", f"{PLUGIN}.faulty", f"{PLUGIN}.form"]
    assert [e["id"] for e in manifest.editor_tools] == [f"{PLUGIN}.tint", f"{PLUGIN}.tint-later", f"{PLUGIN}.faulty", f"{PLUGIN}.form"]
    assert [e["id"] for e in manifest.editor_overlays] == [f"{PLUGIN}.spotlight"]
    assert [e["id"] for e in manifest.editor_tasks] == [f"{PLUGIN}.relight"]
    assert world.registry.enable_plugin(PLUGIN) is True


def test_every_file_the_manifest_points_at_is_in_the_built_bundle(world):
    manifest = world.registry.get_plugin(PLUGIN)
    paths = [e["module"] for e in manifest.editor_steps]
    paths += [e["options"] for e in manifest.editor_tools if e["options"]]
    paths += [e["schema_file"] for e in manifest.editor_tools if e["schema_file"]]
    paths += [e["component"] for e in manifest.editor_overlays]

    assert paths and all((SLOTS_DIST / path).is_file() for path in paths)


def test_the_task_is_listed_for_the_image_editor_once_the_plugin_is_enabled(world, client):
    assert set(listed_tasks(client)) == core_image_tasks(world)

    assert world.registry.enable_plugin(PLUGIN) is True

    listed = listed_tasks(client)
    assert set(listed) == core_image_tasks(world) | {f"{PLUGIN}.relight"}
    assert listed[f"{PLUGIN}.relight"]["label"] == "Relight"
    assert listed[f"{PLUGIN}.relight"]["offers"] == []


def test_the_extension_list_carries_each_part_with_its_plugin_and_dist_relative_paths(world):
    world.registry.enable_plugin(PLUGIN)

    listed = extensions(world)

    assert [(e["plugin_id"], e["id"], e["label"]) for e in listed["editor_groups"]] == [(PLUGIN, f"{PLUGIN}.stylize", "Stylize")]
    assert {e["id"]: e["module"] for e in listed["editor_steps"]} == {
        f"{PLUGIN}.tint": "editor/tintStep.js",
        f"{PLUGIN}.tint-later": "editor/tintStep.js",
        f"{PLUGIN}.faulty": "editor/faultyStep.js",
        f"{PLUGIN}.form": "editor/tintStep.js",
    }
    assert {e["id"]: (e["group"], e["options"]) for e in listed["editor_tools"]} == {
        f"{PLUGIN}.tint": (f"{PLUGIN}.stylize", "editor/TintOptions.js"),
        f"{PLUGIN}.tint-later": (f"{PLUGIN}.stylize", "editor/TintLaterOptions.js"),
        f"{PLUGIN}.faulty": (f"{PLUGIN}.stylize", "editor/FaultyOptions.js"),
        f"{PLUGIN}.form": (f"{PLUGIN}.stylize", None),
    }
    form = next(e for e in listed["editor_tools"] if e["id"] == f"{PLUGIN}.form")
    assert [(f["type"], f["name"]) for f in form["schema"]] == [("slider", "amount"), ("segmented", "mode"), ("checkbox", "keep")]
    assert all("schema" not in e for e in listed["editor_tools"] if e["id"] != f"{PLUGIN}.form")
    assert [(e["id"], e["component"], e["produces"]) for e in listed["editor_overlays"]] == [
        (f"{PLUGIN}.spotlight", "editor/Spotlight.js", "selection")
    ]
    assert [e["id"] for e in listed["editor_tasks"]] == [f"{PLUGIN}.relight"]
    assert listed["revisions"][PLUGIN]


def test_disabling_the_plugin_removes_everything_it_added(world, client):
    world.registry.enable_plugin(PLUGIN)
    world.registry.enable_plugin(OTHER)

    assert world.registry.disable_plugin(PLUGIN) is True

    assert set(listed_tasks(client)) == core_image_tasks(world)
    listed = extensions(world)
    assert all(listed[key] == [] for key in EDITOR_KEYS)
    assert PLUGIN not in listed["revisions"]
    assert [tab["plugin_id"] for tab in listed["tool_tabs"]] == [OTHER]


def test_a_plugin_without_editor_keys_adds_none_and_changes_no_task(world, client):
    manifest = world.registry.get_plugin(OTHER)
    assert all(getattr(manifest, key) == [] for key in EDITOR_KEYS)

    assert world.registry.enable_plugin(OTHER) is True

    assert set(listed_tasks(client)) == core_image_tasks(world)
    listed = extensions(world)
    assert all(listed[key] == [] for key in EDITOR_KEYS)
    assert [tab["plugin_id"] for tab in listed["tool_tabs"]] == [OTHER]
