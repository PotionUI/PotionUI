"""A plugin whose router won't mount at startup must not stay enabled.

Startup brings plugins the database has enabled back up in the registry before
the FastAPI app exists, so their routers are only mounted later, when
`mount_plugin_routers` attaches the app. A failure there is discovered after
`enable_plugin` already returned True: without a lifecycle rule the plugin sits
ENABLED with live hooks, no routes, and an `enabled` row in the database that
says everything is fine. These tests pin the rule that such a plugin is taken
down to ERROR and disabled, and that it comes back cleanly once fixed.
"""

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.bootstrap.app import mount_plugin_routers
from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.plugins.router_mounter import PluginRouterMounter


WORKING_API_MODULE = """
from fastapi import APIRouter

router = APIRouter(prefix="/api/plugins/{plugin_id}", tags=["Test"])


@router.get("/ping")
def ping():
    return {{"ok": True}}
"""

# Mounts its HTTP router, then blows up on the WebSocket router.
BROKEN_API_MODULE = """
from fastapi import APIRouter

router = APIRouter(prefix="/api/plugins/{plugin_id}", tags=["Test"])


@router.get("/ping")
def ping():
    return {{"ok": True}}


class _ExplodingRouter:
    prefix = "/api/plugins/{plugin_id}"

    @property
    def routes(self):
        raise RuntimeError("ws_router is broken")


ws_router = _ExplodingRouter()
"""

HANDLER_SOURCE = """
def on_boot(context):
    return context
"""


class FakePluginRepository:
    """The `disable_plugin` surface the startup mount path writes back through."""

    def __init__(self):
        self.enabled = {}

    def disable_plugin(self, plugin_id):
        self.enabled[plugin_id] = False
        return True


@pytest.fixture
def startup_env():
    temp_dir = Path(tempfile.mkdtemp())
    marketplace_dir = temp_dir / "marketplace"
    marketplace_dir.mkdir()
    (temp_dir / "local").mkdir()

    def create_plugin(plugin_id, api_source):
        plugin_dir = marketplace_dir / plugin_id
        plugin_dir.mkdir()
        backend_dir = plugin_dir / "backend"
        backend_dir.mkdir()
        (plugin_dir / "hooks.py").write_text(HANDLER_SOURCE)
        (backend_dir / "api.py").write_text(api_source.format(plugin_id=plugin_id))
        (plugin_dir / "manifest.yml").write_text(yaml.dump({
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': f'{plugin_id} startup fixture',
            'author': 'Test Author',
            'type': 'full-stack',
            'api': {'module': 'backend/api.py'},
            'hooks': {
                'backend': [
                    {'hook': 'plugin.lifecycle.boot', 'handler': 'hooks.on_boot'}
                ]
            },
        }))
        return plugin_dir

    # The mounter is deliberately unattached: this is the startup ordering,
    # where plugins are enabled from the database before the app is built.
    router_mounter = PluginRouterMounter()
    registry = PluginRegistry(
        str(marketplace_dir), str(temp_dir / "local"), router_mounter=router_mounter
    )
    repository = FakePluginRepository()

    yield SimpleNamespace(
        create_plugin=create_plugin,
        registry=registry,
        router_mounter=router_mounter,
        container=SimpleNamespace(
            plugin_registry=registry,
            plugin_router_mounter=router_mounter,
            plugin_repository=repository,
        ),
        repository=repository,
        marketplace_dir=marketplace_dir,
    )

    shutil.rmtree(temp_dir)


def _hook_owners(registry, hook_name):
    return [pid for pid, _ in registry.hook_chain._handlers.get(hook_name, [])]


def test_deferred_mount_failure_takes_the_plugin_down(startup_env):
    startup_env.create_plugin("broken-ws", BROKEN_API_MODULE)
    startup_env.repository.enabled["broken-ws"] = True

    # Startup enable: no app yet, so the mount is deferred and this succeeds.
    assert startup_env.registry.enable_plugin("broken-ws") is True
    assert startup_env.registry.get_plugin_state("broken-ws") == PluginState.ENABLED

    app = FastAPI()
    mount_plugin_routers(app, startup_env.container)

    assert startup_env.registry.get_plugin_state("broken-ws") == PluginState.ERROR
    assert "mount" in (startup_env.registry.get_plugin_error("broken-ws") or "").lower()
    assert _hook_owners(startup_env.registry, "plugin.lifecycle.boot") == []
    assert startup_env.router_mounter.is_mounted("broken-ws") is False
    assert TestClient(app).get("/api/plugins/broken-ws/ping").status_code == 404
    # The database must not keep calling it enabled for the next boot.
    assert startup_env.repository.enabled["broken-ws"] is False


def test_deferred_mount_failure_leaves_a_healthy_plugin_mounted(startup_env):
    startup_env.create_plugin("broken-ws", BROKEN_API_MODULE)
    startup_env.create_plugin("healthy", WORKING_API_MODULE)
    startup_env.repository.enabled.update({"broken-ws": True, "healthy": True})

    assert startup_env.registry.enable_plugin("broken-ws") is True
    assert startup_env.registry.enable_plugin("healthy") is True

    app = FastAPI()
    mount_plugin_routers(app, startup_env.container)

    client = TestClient(app)
    assert client.get("/api/plugins/healthy/ping").status_code == 200
    assert client.get("/api/plugins/broken-ws/ping").status_code == 404
    assert startup_env.registry.get_plugin_state("healthy") == PluginState.ENABLED
    assert _hook_owners(startup_env.registry, "plugin.lifecycle.boot") == ["healthy"]
    assert "healthy" not in startup_env.repository.enabled or \
        startup_env.repository.enabled["healthy"] is True


def test_plugin_recovers_once_the_router_is_fixed(startup_env):
    plugin_dir = startup_env.create_plugin("broken-ws", BROKEN_API_MODULE)
    startup_env.repository.enabled["broken-ws"] = True

    startup_env.registry.enable_plugin("broken-ws")
    mount_plugin_routers(FastAPI(), startup_env.container)
    assert startup_env.registry.get_plugin_state("broken-ws") == PluginState.ERROR

    # The admin fixes the plugin and enables it again - now with an app attached.
    (plugin_dir / "backend" / "api.py").write_text(
        WORKING_API_MODULE.format(plugin_id="broken-ws")
    )
    app = FastAPI()
    startup_env.router_mounter.attach(app)

    assert startup_env.registry.enable_plugin("broken-ws") is True
    assert startup_env.registry.get_plugin_state("broken-ws") == PluginState.ENABLED
    assert startup_env.registry.get_plugin_error("broken-ws") is None
    assert _hook_owners(startup_env.registry, "plugin.lifecycle.boot") == ["broken-ws"]
    assert TestClient(app).get("/api/plugins/broken-ws/ping").status_code == 200
