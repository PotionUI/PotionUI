import os
import shutil
import sys

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.media_tools import MediaToolRegistry
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.plugins.router_mounter import PluginRouterMounter

ROUTER_ID = "upd-router"
TOOLS_ID = "upd-tools"

API_SOURCE = """
from fastapi import APIRouter

from . import VERSION
from .common import {names}

router = APIRouter(prefix="/api/plugins/upd-router")


@router.get("/who")
def who():
    from .late import WHO as LATE
    return {{"package": VERSION, "common": WHO, "late": LATE}}
"""

IMAGE_SOURCE = """
from src.plugin_api.media_tools import FilterFragment, MediaTool

from .common import {names}


class ConvertImage(MediaTool):
    VERSION = WHO

    def video_filter(self, media, params):
        return FilterFragment(vf=["null"])
"""


def router_files(version: str, *, added: bool) -> dict:
    common = f'WHO = "{version}"\n' + ("ADDED = True\n" if added else "")
    return {
        "manifest.yml": yaml.safe_dump({
            "id": ROUTER_ID,
            "name": ROUTER_ID,
            "version": version,
            "description": "Update test",
            "author": "Test",
            "type": "full-stack",
            "api": {"module": "backend/api.py"},
        }),
        "backend/__init__.py": f'VERSION = "{version}"\n',
        "backend/common.py": common,
        "backend/late.py": f'WHO = "{version}"\n',
        "backend/api.py": API_SOURCE.format(names="ADDED, WHO" if added else "WHO"),
    }


def tools_files(version: str, *, added: bool) -> dict:
    common = f'WHO = "{version}"\nGRANTS = {{}}\n' + ("\n\ndef release(key):\n    GRANTS.pop(key, None)\n" if added else "")
    return {
        "manifest.yml": yaml.safe_dump({
            "id": TOOLS_ID,
            "name": TOOLS_ID,
            "version": version,
            "description": "Update test",
            "author": "Test",
            "type": "backend-only",
            "media_tools": [{
                "id": f"{TOOLS_ID}.image",
                "label": "Convert image",
                "accepts": ["image"],
                "python": "tools/image.py:ConvertImage",
            }],
        }),
        "tools/__init__.py": "",
        "tools/common.py": common,
        "tools/image.py": IMAGE_SOURCE.format(names="WHO, release" if added else "WHO"),
    }


def put_tree(folder, files: dict) -> None:
    staged = folder.with_name(folder.name + ".staged")
    for relative, source in files.items():
        target = staged / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    if folder.exists():
        shutil.rmtree(folder)
    os.replace(staged, folder)


@pytest.fixture(autouse=True)
def clean_imports():
    modules, path = set(sys.modules), list(sys.path)
    yield
    for name in set(sys.modules) - modules:
        del sys.modules[name]
    sys.path[:] = path


@pytest.fixture
def setup(tmp_path):
    marketplace, local = tmp_path / "marketplace", tmp_path / "local"
    marketplace.mkdir()
    local.mkdir()
    app = FastAPI()
    mounter = PluginRouterMounter()
    mounter.attach(app)
    media_tools = MediaToolRegistry()
    registry = PluginRegistry(
        str(marketplace), str(local), router_mounter=mounter, media_tool_registry=media_tools
    )
    return registry, local, TestClient(app), media_tools


def enabled(registry, local, plugin_id, files):
    put_tree(local / plugin_id, files)
    registry.discover_plugins()
    assert registry.enable_plugin(plugin_id), registry.get_plugin_error(plugin_id)


def test_a_rescan_that_updates_an_enabled_plugin_runs_every_module_of_the_new_version(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("1.0.0", added=False))
    assert client.get("/api/plugins/upd-router/who").json() == {"package": "1.0.0", "common": "1.0.0", "late": "1.0.0"}

    put_tree(local / ROUTER_ID, router_files("2.0.0", added=True))
    changes = registry.discover_plugins()

    assert changes.changed == [ROUTER_ID] and changes.errored == []
    assert registry.get_plugin_state(ROUTER_ID) == PluginState.ENABLED, registry.get_plugin_error(ROUTER_ID)
    assert client.get("/api/plugins/upd-router/who").json() == {"package": "2.0.0", "common": "2.0.0", "late": "2.0.0"}


def test_a_plugin_updated_while_disabled_runs_the_new_version_once_enabled(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("1.0.0", added=False))
    assert client.get("/api/plugins/upd-router/who").status_code == 200
    assert registry.disable_plugin(ROUTER_ID)

    put_tree(local / ROUTER_ID, router_files("2.0.0", added=True))
    registry.discover_plugins()

    assert registry.get_plugin_state(ROUTER_ID) == PluginState.DISABLED
    assert registry.enable_plugin(ROUTER_ID), registry.get_plugin_error(ROUTER_ID)
    assert client.get("/api/plugins/upd-router/who").json() == {"package": "2.0.0", "common": "2.0.0", "late": "2.0.0"}


def test_going_back_to_the_previous_version_runs_none_of_the_newer_modules(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("2.0.0", added=True))
    assert client.get("/api/plugins/upd-router/who").json()["late"] == "2.0.0"

    put_tree(local / ROUTER_ID, router_files("1.0.0", added=False))
    registry.discover_plugins()

    assert registry.get_plugin_state(ROUTER_ID) == PluginState.ENABLED
    assert client.get("/api/plugins/upd-router/who").json() == {"package": "1.0.0", "common": "1.0.0", "late": "1.0.0"}
    assert not hasattr(sys.modules["upd_router.backend.common"], "ADDED")


def test_disabling_and_enabling_an_unchanged_plugin_keeps_the_modules_it_imported(setup):
    registry, local, _, media_tools = setup
    enabled(registry, local, TOOLS_ID, tools_files("1.0.0", added=True))
    common = sys.modules["upd_tools.tools.common"]
    common.GRANTS["running"] = 1

    assert registry.disable_plugin(TOOLS_ID)
    assert registry.enable_plugin(TOOLS_ID)

    assert sys.modules["upd_tools.tools.common"] is common
    assert common.GRANTS == {"running": 1}
    assert media_tools.get(f"{TOOLS_ID}.image").impl.VERSION == "1.0.0"


def test_a_reload_runs_the_files_as_they_are_on_disk(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("1.0.0", added=False))
    assert client.get("/api/plugins/upd-router/who").json()["common"] == "1.0.0"
    files = router_files("1.0.0", added=True)
    files["backend/common.py"] = 'WHO = "edited"\nADDED = True\n'
    put_tree(local / ROUTER_ID, files)

    assert registry.reload_plugin(ROUTER_ID), registry.get_plugin_error(ROUTER_ID)

    assert client.get("/api/plugins/upd-router/who").json()["common"] == "edited"


def test_a_rescan_that_updates_an_enabled_media_tool_plugin_registers_the_new_tool(setup):
    registry, local, _, media_tools = setup
    enabled(registry, local, TOOLS_ID, tools_files("1.0.0", added=False))
    assert media_tools.get(f"{TOOLS_ID}.image").impl.VERSION == "1.0.0"

    put_tree(local / TOOLS_ID, tools_files("1.1.0", added=True))
    changes = registry.discover_plugins()

    assert changes.errored == []
    assert registry.get_plugin_state(TOOLS_ID) == PluginState.ENABLED, registry.get_plugin_error(TOOLS_ID)
    assert media_tools.get(f"{TOOLS_ID}.image").impl.VERSION == "1.1.0"
    assert callable(sys.modules["upd_tools.tools.common"].release)


def test_a_plugin_that_moves_to_another_folder_runs_from_that_folder(setup, tmp_path):
    registry, local, client, _ = setup
    marketplace = tmp_path / "marketplace"
    enabled(registry, marketplace, ROUTER_ID, router_files("1.0.0", added=False))
    assert client.get("/api/plugins/upd-router/who").json()["late"] == "1.0.0"

    put_tree(local / ROUTER_ID, router_files("2.0.0", added=True))
    registry.discover_plugins()

    assert registry.get_plugin(ROUTER_ID).plugin_dir == local / ROUTER_ID
    assert registry.get_plugin_state(ROUTER_ID) == PluginState.ENABLED, registry.get_plugin_error(ROUTER_ID)
    assert client.get("/api/plugins/upd-router/who").json() == {"package": "2.0.0", "common": "2.0.0", "late": "2.0.0"}
