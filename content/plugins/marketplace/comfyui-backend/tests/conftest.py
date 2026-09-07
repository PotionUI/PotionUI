import sys
import os
import importlib

import pytest

# Add the comfyui-backend plugin directory to sys.path so `from backend.xxx`
# resolves to this plugin's backend package.
#
# IMPORTANT: Other plugin tests (e.g. downloader) may have already imported a
# different 'backend' package at collection time. We need to:
# 1. Remove stale 'backend' entries from sys.modules
# 2. Ensure our path is first in sys.path
# 3. Re-import the 'backend' package from our path

plugin_dir = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'content', 'plugins', 'marketplace', 'comfyui-backend'
))

# Remove any previously cached 'backend' and all its submodules
for key in list(sys.modules.keys()):
    if key == 'backend' or key.startswith('backend.'):
        del sys.modules[key]

# Ensure our plugin dir is at the very front of sys.path
if plugin_dir in sys.path:
    sys.path.remove(plugin_dir)
sys.path.insert(0, plugin_dir)

# Invalidate import caches and pre-import backend to claim the namespace
importlib.invalidate_caches()
import backend  # noqa: F401 - ensures backend resolves to our plugin


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "uses_object_info_mock: opts out of the object-info network-isolation "
        "autouse fixture below - the test mocks backend.api._fetch_object_info/"
        "_get_comfyui_base_url itself instead of hitting a real server.",
    )


@pytest.fixture(autouse=True)
def _isolate_base_url_from_the_database(monkeypatch):
    """`backend.api._get_comfyui_base_url` reads the plugin's settings rows
    from the live database; no test here may touch one (an unmigrated
    scratch database has no `plugin_settings` table, a real one leaks the
    machine's configuration into the test). Every test gets the plugin's
    default URL; a test that needs another still monkeypatches it itself.
    """
    try:
        import backend.api as api_module
    except Exception:
        return
    monkeypatch.setattr(api_module, "_get_comfyui_base_url", lambda: "http://127.0.0.1:8188", raising=False)


@pytest.fixture(autouse=True)
def _isolate_object_info_from_the_network(request, monkeypatch):
    """No test in this suite may depend on a real ComfyUI server answering
    `/object_info` - `backend.api.analyze_workflow`/`.get_imported_preset_source`
    make a best-effort LIVE fetch when a backend is configured (see api.py's
    `_try_object_info`), and something on this machine answers on the
    plugin's default host/port (127.0.0.1:8188) whether or not that's
    intentional. Every test gets `_try_object_info` stubbed to always return
    `None` (the "no backend reachable" case, exercised elsewhere without any
    network dependency); a test that exercises the enrichment path itself
    opts out with `@pytest.mark.uses_object_info_mock` and mocks the fetch
    at a lower level instead (see test_import_object_info_enrichment.py) -
    never a live server, in this test or any other.
    """
    if request.node.get_closest_marker("uses_object_info_mock"):
        return
    try:
        import backend.api as api_module
    except Exception:
        return

    async def _no_object_info():
        return None

    monkeypatch.setattr(api_module, "_try_object_info", _no_object_info, raising=False)
