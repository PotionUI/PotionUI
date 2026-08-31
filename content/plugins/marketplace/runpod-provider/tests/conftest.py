"""Test fixtures for the runpod-provider plugin.

Mirrors `PluginLoader.load_plugin_module`'s runtime behavior (puts the
plugin dir on `sys.path` before loading its `api:` module) - the same idiom
`comfyui_backend`/`spritesheet`'s own conftests use, including evicting any
`backend` module another plugin's test directory already claimed. Run this
directory on its own or alongside other single-`backend`-package plugin
tests in separate invocations, not as sibling args in one pytest call - see
the spritesheet conftest's docstring for why.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "runpod-provider"

for _key in list(sys.modules):
    if _key == "backend" or _key.startswith("backend."):
        del sys.modules[_key]

if str(PLUGIN_ROOT) in sys.path:
    sys.path.remove(str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

import importlib
importlib.invalidate_caches()
import backend  # noqa: E402,F401  (claims the namespace for this plugin)


@pytest.fixture
def scratch_db(tmp_path):
    """A throwaway sqlite file for exactly one test - never the real app DB.
    Mirrors the spritesheet plugin's own `scratch_db` fixture: a fresh
    `Database` pointed at a temp file, no migration replay (this plugin's
    table has no FOREIGN KEY on anything migration-owned)."""
    from src.plugin_api.storage import db

    Database = type(db)

    Database._instance = None
    test_db = Database()
    test_db.db_path = tmp_path / "test.sqlite"
    test_db.db_path.parent.mkdir(parents=True, exist_ok=True)
    test_db._initialized = True
    yield test_db
    Database._instance = None
