import importlib
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

for _key in list(sys.modules):
    if _key.split(".", 1)[0] in ("backend", "hooks"):
        del sys.modules[_key]

if str(PLUGIN_ROOT) in sys.path:
    sys.path.remove(str(PLUGIN_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))

importlib.invalidate_caches()
import backend  # noqa: E402,F401


@pytest.fixture
def scratch_db(tmp_path):
    from src.plugin_api import scratch_database

    return scratch_database(tmp_path / "test.sqlite", core_schema=False)


@pytest.fixture
def plugin_db(scratch_db):
    from src.plugin_api import use_database

    with use_database(scratch_db):
        yield scratch_db


@pytest.fixture
def scheduled(monkeypatch):
    import concurrent.futures

    import src.plugin_api.hooks as plugin_hooks

    jobs = []

    def run_on_app_loop(coro):
        jobs.append(coro)
        return concurrent.futures.Future()

    monkeypatch.setattr(plugin_hooks, "run_on_app_loop", run_on_app_loop)
    yield jobs
    for job in jobs:
        job.close()
