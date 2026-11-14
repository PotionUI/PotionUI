import builtins
import importlib
import importlib.util
import os
import shutil
import stat
import sys
import threading
import time

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.platform.plugins.loader as loader_module
from src.pipelines.catalog import PipeCatalog
from src.platform.media_tools import MediaToolRegistry
from src.platform.plugins.loader import forget_modules_under
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


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def link_folder(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("no directory link available")


def test_a_code_only_edit_is_loaded_at_the_next_enable(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("1.0.0", added=False))
    assert client.get("/api/plugins/upd-router/who").status_code == 200
    assert registry.disable_plugin(ROUTER_ID)
    files = router_files("1.0.0", added=True)
    files["backend/late.py"] = 'WHO = "edited"\n'
    put_tree(local / ROUTER_ID, files)

    assert registry.discover_plugins().changed == []
    assert registry.enable_plugin(ROUTER_ID), registry.get_plugin_error(ROUTER_ID)

    assert client.get("/api/plugins/upd-router/who").json() == {"package": "1.0.0", "common": "1.0.0", "late": "edited"}
    assert sys.modules["upd_router.backend.common"].ADDED is True


def test_a_plugin_that_left_the_registry_starts_again_from_its_files_when_it_comes_back(setup, tmp_path):
    registry, local, _, _ = setup
    enabled(registry, local, TOOLS_ID, tools_files("1.0.0", added=True))
    first = sys.modules["upd_tools.tools.common"]
    first.GRANTS["running"] = 1
    os.replace(local / TOOLS_ID, tmp_path / "away")
    assert registry.discover_plugins().removed == [TOOLS_ID]

    os.replace(tmp_path / "away", local / TOOLS_ID)
    registry.discover_plugins()
    assert registry.enable_plugin(TOOLS_ID), registry.get_plugin_error(TOOLS_ID)

    assert sys.modules["upd_tools.tools.common"] is not first
    assert sys.modules["upd_tools.tools.common"].GRANTS == {}


def test_a_plugin_moved_out_of_a_folder_leaves_the_plugin_now_living_there_alone(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("1.0.0", added=False))
    assert registry.disable_plugin(ROUTER_ID)
    os.replace(local / ROUTER_ID, local / "moved")
    put_tree(local / ROUTER_ID, tools_files("1.0.0", added=True))
    registry.discover_plugins()
    assert registry.enable_plugin(TOOLS_ID), registry.get_plugin_error(TOOLS_ID)
    common = sys.modules["upd_tools.tools.common"]

    assert registry.enable_plugin(ROUTER_ID), registry.get_plugin_error(ROUTER_ID)

    assert registry.get_plugin(ROUTER_ID).plugin_dir == local / "moved"
    assert sys.modules["upd_tools.tools.common"] is common
    assert client.get("/api/plugins/upd-router/who").json() == {"package": "1.0.0", "common": "1.0.0", "late": "1.0.0"}
    assert sys.modules["upd_router.backend.common"].__file__.startswith(str(local / "moved"))


def test_forgetting_a_folder_keeps_the_modules_of_a_folder_whose_name_extends_it(tmp_path):
    (tmp_path / "plug").mkdir()
    (tmp_path / "plug-extra").mkdir()
    (tmp_path / "plug" / "m.py").write_text("X = 1\n")
    (tmp_path / "plug-extra" / "m.py").write_text("X = 2\n")
    load_file("upd_inside", tmp_path / "plug" / "m.py")
    load_file("upd_sibling", tmp_path / "plug-extra" / "m.py")

    assert forget_modules_under([str(tmp_path / "plug")]) == ["upd_inside"]
    assert "upd_sibling" in sys.modules


def test_forgetting_a_folder_keeps_a_namespace_package_that_spans_another_folder_and_reloads_its_child(tmp_path):
    for root in ("plug", "other"):
        (tmp_path / root / "upd_shared").mkdir(parents=True)
    (tmp_path / "plug" / "upd_shared" / "child.py").write_text('WHO = "old"\n')
    (tmp_path / "other" / "upd_shared" / "sibling.py").write_text('WHO = "other"\n')
    sys.path[:0] = [str(tmp_path / "plug"), str(tmp_path / "other")]
    importlib.invalidate_caches()
    package = importlib.import_module("upd_shared")
    importlib.import_module("upd_shared.child")
    importlib.import_module("upd_shared.sibling")
    (tmp_path / "plug" / "upd_shared" / "child.py").write_text('WHO = "new, and longer"\n')

    forgotten = forget_modules_under([str(tmp_path / "plug")])

    assert forgotten == ["upd_shared.child"]
    assert sys.modules["upd_shared"] is package
    namespace = {}
    exec("from upd_shared import child", namespace)
    assert namespace["child"].WHO == "new, and longer"


def test_forgetting_a_folder_leaves_a_module_another_thread_is_still_importing(tmp_path):
    folder = tmp_path / "plug-race"
    folder.mkdir()
    (folder / "upd_slow.py").write_text(
        "import builtins\nbuiltins._upd_started.set()\nbuiltins._upd_go.wait(10)\nVALUE = 1\n"
    )
    builtins._upd_started, builtins._upd_go = threading.Event(), threading.Event()
    sys.path.insert(0, str(folder))
    importlib.invalidate_caches()
    outcome = {}

    def worker():
        try:
            outcome["value"] = importlib.import_module("upd_slow").VALUE
        except BaseException as error:
            outcome["value"] = type(error).__name__

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert builtins._upd_started.wait(10)
        forgotten = forget_modules_under([str(folder)])
    finally:
        builtins._upd_go.set()
        thread.join(10)
        del builtins._upd_started, builtins._upd_go

    assert forgotten == []
    assert outcome["value"] == 1


@pytest.mark.parametrize("module_side", ["link", "real"])
def test_forgetting_a_folder_follows_a_linked_folder_either_way(tmp_path, module_side):
    real = tmp_path / "real-plugin"
    real.mkdir()
    (real / "m.py").write_text("X = 1\n")
    link = tmp_path / "linked-plugin"
    link_folder(link, real)
    module_folder, root = (link, real) if module_side == "link" else (real, link)
    load_file("upd_linked", module_folder / "m.py")

    assert forget_modules_under([str(root)]) == ["upd_linked"]


@pytest.mark.skipif(os.name != "nt", reason="short names exist on Windows only")
def test_forgetting_a_folder_follows_its_short_name_either_way(tmp_path):
    import ctypes

    folder = tmp_path / "a-rather-long-plugin-folder-name"
    folder.mkdir()
    (folder / "m.py").write_text("X = 1\n")
    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(folder), buffer, 1024)
    short = buffer.value
    if not short or os.path.normcase(short) == os.path.normcase(str(folder)):
        pytest.skip("this volume keeps no short names")
    for module_folder, root in ((short, str(folder)), (str(folder), short)):
        load_file("upd_short", os.path.join(module_folder, "m.py"))
        assert forget_modules_under([root]) == ["upd_short"]


def test_an_edit_of_a_module_imported_after_the_enable_is_loaded_at_the_next_enable(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("1.0.0", added=False))
    assert client.get("/api/plugins/upd-router/who").json()["late"] == "1.0.0"
    assert registry.disable_plugin(ROUTER_ID)
    time.sleep(0.05)
    (local / ROUTER_ID / "backend" / "late.py").write_text('WHO = "edited"\n')

    assert registry.enable_plugin(ROUTER_ID), registry.get_plugin_error(ROUTER_ID)

    assert client.get("/api/plugins/upd-router/who").json()["late"] == "edited"


def test_a_plugin_moved_out_of_a_folder_another_plugin_now_holds_runs_from_its_new_folder(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("1.0.0", added=False))
    assert client.get("/api/plugins/upd-router/who").json()["late"] == "1.0.0"
    assert registry.disable_plugin(ROUTER_ID)
    os.replace(local / ROUTER_ID, local / "moved")
    put_tree(local / ROUTER_ID, tools_files("1.0.0", added=True))
    registry.discover_plugins()

    assert registry.enable_plugin(ROUTER_ID), registry.get_plugin_error(ROUTER_ID)

    assert client.get("/api/plugins/upd-router/who").json() == {"package": "1.0.0", "common": "1.0.0", "late": "1.0.0"}
    for name in ("upd_router", "upd_router.backend", "upd_router.backend.common", "upd_router.backend.late"):
        module = sys.modules[name]
        location = getattr(module, "__file__", None) or list(module.__path__)[0]
        assert location.startswith(str(local / "moved")), name


def side_files(plugin_id: str, version: str, side: str, body: str, *, imported: bool = True) -> dict:
    return {
        "manifest.yml": yaml.safe_dump({
            "id": plugin_id,
            "name": plugin_id,
            "version": version,
            "description": "Update test",
            "author": "Test",
            "type": "full-stack",
            "api": {"module": "backend/api.py"},
        }),
        "backend/__init__.py": "",
        "backend/api.py": (f"import {side}\n" if imported else "")
        + f'from fastapi import APIRouter\n\nrouter = APIRouter(prefix="/api/plugins/{plugin_id}")\n',
        f"{side}.py": body,
    }


ALIAS_PIPE_SOURCE = """
from src.pipelines.contracts import BasePipe, PipeOutput


def _make():
    class Generated(BasePipe):
        name = "alias_pipe"
        description = "1.0.0"

        def process(self, pipe_input, generation_outputs):
            return PipeOutput(output={})

        @classmethod
        def inputs(cls):
            return []

        @classmethod
        def outputs(cls):
            return []

        @classmethod
        def configuration(cls):
            return []

    return Generated


AliasPipe = _make()
"""


def rewrite_in_place(path, text, *, keep_date=False):
    before = os.stat(path)
    time.sleep(0.05)
    with open(path, "w") as handle:
        handle.write(text)
    if keep_date:
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    return before


def test_a_mode_change_alone_keeps_the_modules_of_an_unchanged_plugin(setup):
    registry, local, _, _ = setup
    enabled(registry, local, TOOLS_ID, tools_files("1.0.0", added=True))
    common = sys.modules["upd_tools.tools.common"]
    common.GRANTS["running"] = 1
    assert registry.disable_plugin(TOOLS_ID)
    target = local / TOOLS_ID / "tools" / "common.py"
    mode = stat.S_IMODE(os.stat(target).st_mode)
    time.sleep(0.05)
    os.chmod(target, stat.S_IREAD)
    os.chmod(target, mode)

    assert registry.enable_plugin(TOOLS_ID), registry.get_plugin_error(TOOLS_ID)

    assert sys.modules["upd_tools.tools.common"] is common
    assert common.GRANTS == {"running": 1}


def test_a_rewrite_that_keeps_the_size_is_loaded_at_the_next_enable(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("1.0.0", added=False))
    assert registry.disable_plugin(ROUTER_ID)
    target = local / ROUTER_ID / "backend" / "common.py"
    before = rewrite_in_place(target, 'WHO = "9.9.9"\n')
    assert os.stat(target).st_size == before.st_size

    assert registry.enable_plugin(ROUTER_ID), registry.get_plugin_error(ROUTER_ID)

    assert client.get("/api/plugins/upd-router/who").json()["common"] == "9.9.9"


def test_a_rewrite_that_keeps_the_date_is_loaded_at_the_next_enable(setup):
    registry, local, client, _ = setup
    enabled(registry, local, ROUTER_ID, router_files("1.0.0", added=False))
    assert registry.disable_plugin(ROUTER_ID)
    target = local / ROUTER_ID / "backend" / "common.py"
    before = rewrite_in_place(target, 'WHO = "kept its date"\n', keep_date=True)
    assert os.stat(target).st_mtime_ns == before.st_mtime_ns

    assert registry.enable_plugin(ROUTER_ID), registry.get_plugin_error(ROUTER_ID)

    assert client.get("/api/plugins/upd-router/who").json()["common"] == "kept its date"


def test_a_module_spared_while_another_thread_imported_it_is_loaded_at_the_next_enable(setup):
    registry, local, _, _ = setup
    slow = "import builtins\nbuiltins._upd_started.set()\nbuiltins._upd_go.wait(10)\nVALUE = 1\n"
    enabled(registry, local, "upd-slow", side_files("upd-slow", "1.0.0", "upd_slow_side", slow, imported=False))
    builtins._upd_started, builtins._upd_go = threading.Event(), threading.Event()
    importlib.invalidate_caches()
    outcome = {}

    def worker():
        try:
            outcome["value"] = importlib.import_module("upd_slow_side").VALUE
        except BaseException as error:
            outcome["value"] = type(error).__name__

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert builtins._upd_started.wait(10)
        put_tree(local / "upd-slow", side_files("upd-slow", "2.0.0", "upd_slow_side", "VALUE = 2\n", imported=False))
        registry.discover_plugins()
    finally:
        builtins._upd_go.set()
        thread.join(10)
        del builtins._upd_started, builtins._upd_go
    assert outcome["value"] == 1
    assert sys.modules["upd_slow_side"].VALUE == 1

    assert registry.disable_plugin("upd-slow")
    assert registry.enable_plugin("upd-slow"), registry.get_plugin_error("upd-slow")

    assert importlib.import_module("upd_slow_side").VALUE == 2


def test_a_plugin_inside_the_folder_of_another_reloads_all_its_modules(tmp_path, monkeypatch):
    marketplace, local = tmp_path / "marketplace", tmp_path / "local"
    inner_root = local / "upd-outer" / "plugins"
    inner_root.mkdir(parents=True)
    marketplace.mkdir()
    monkeypatch.setenv("POTIONUI_EXTRA_PLUGIN_DIRS", str(inner_root))
    mounter = PluginRouterMounter()
    mounter.attach(FastAPI())
    registry = PluginRegistry(str(marketplace), str(local), router_mounter=mounter)
    (local / "upd-outer" / "manifest.yml").write_text(yaml.safe_dump({
        "id": "upd-outer", "name": "upd-outer", "version": "1.0.0", "description": "d", "author": "a", "type": "backend-only",
    }))
    put_tree(inner_root / "upd-inner", side_files("upd-inner", "1.0.0", "upd_inner_side", 'WHO = "first"\n'))
    registry.discover_plugins()
    assert registry.get_plugin("upd-inner") is not None
    assert registry.enable_plugin("upd-inner"), registry.get_plugin_error("upd-inner")

    put_tree(inner_root / "upd-inner", side_files("upd-inner", "2.0.0", "upd_inner_side", 'WHO = "second"\n'))
    registry.discover_plugins()

    assert registry.get_plugin_state("upd-inner") == PluginState.ENABLED
    assert sys.modules["upd_inner_side"].WHO == "second"


def test_a_plugin_moved_out_of_a_folder_another_plugin_takes_reloads_all_its_modules(setup):
    registry, local, _, _ = setup
    put_tree(local / "first-home", side_files("upd-mover", "1.0.0", "upd_mover_side", 'WHO = "old"\n'))
    registry.discover_plugins()
    assert registry.enable_plugin("upd-mover"), registry.get_plugin_error("upd-mover")
    assert registry.disable_plugin("upd-mover")
    shutil.rmtree(local / "first-home")
    put_tree(local / "second-home", side_files("upd-mover", "2.0.0", "upd_mover_side", 'WHO = "new"\n'))
    put_tree(local / "first-home", router_files("1.0.0", added=False))
    registry.discover_plugins()

    assert registry.enable_plugin("upd-mover"), registry.get_plugin_error("upd-mover")

    assert sys.modules["upd_mover_side"].WHO == "new"


def test_a_pipe_class_bound_under_another_name_is_kept_across_rescans(setup, tmp_path):
    registry, local, _, _ = setup
    (tmp_path / "core").mkdir()
    (tmp_path / "custom").mkdir()
    pipes = PipeCatalog(str(tmp_path / "core"), str(tmp_path / "custom"), plugin_registry=registry)
    put_tree(local / "upd-alias", {
        "manifest.yml": yaml.safe_dump({
            "id": "upd-alias", "name": "upd-alias", "version": "1.0.0", "description": "d", "author": "a",
            "type": "backend-only", "pipes": [{"path": "pipes/gen", "register_as": "generator/upd_alias"}],
        }),
        "pipes/gen/main.py": ALIAS_PIPE_SOURCE,
    })
    registry.discover_plugins()
    assert registry.enable_plugin("upd-alias"), registry.get_plugin_error("upd-alias")
    pipes.rescan_plugin_pipes()
    first = pipes.get_pipe("generator/upd_alias")
    assert first is not None and first.__name__ == "Generated"

    pipes.rescan_plugin_pipes()
    pipes.rescan_plugin_pipes()

    assert pipes.get_pipe("generator/upd_alias") is first


def test_forgetting_a_folder_under_a_spared_prefix_still_follows_a_linked_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(loader_module, "_UNMOVED", (os.path.normcase(os.path.abspath(str(tmp_path))),))
    real = tmp_path / "real-plugin"
    real.mkdir()
    (real / "m.py").write_text("X = 1\n")
    link = tmp_path / "linked-plugin"
    link_folder(link, real)
    load_file("upd_spared", link / "m.py")

    assert forget_modules_under([str(real)]) == ["upd_spared"]
