"""The startup plugin resync fires `plugin.lifecycle.boot`, not `enable`.

`_sync_enabled_plugins` brings plugins the database has enabled back up in the
registry on every process start. Registering their handlers is all it used to
do, so a plugin doing its initialization in `plugin.lifecycle.enable` was
initialized exactly once - at the admin's original enable - and never again
across restarts. These tests pin the boot dispatch that closes that gap, and
that `enable` still does NOT re-fire on a restart.
"""

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.bootstrap.container import _sync_enabled_plugins
from src.platform.plugins.registry import PluginRegistry, PluginState


class FakePluginRepository:
    """Just the `get_all_plugins()` surface `_sync_enabled_plugins` reads."""

    def __init__(self, plugins):
        self._plugins = plugins

    def get_all_plugins(self):
        return self._plugins


@pytest.fixture
def plugin_env():
    temp_dir = Path(tempfile.mkdtemp())
    marketplace_dir = temp_dir / "marketplace"
    marketplace_dir.mkdir()
    (temp_dir / "local").mkdir()
    log_file = temp_dir / "lifecycle.log"

    def create_plugin(plugin_id, hooks, failing=()):
        plugin_dir = marketplace_dir / plugin_id
        plugin_dir.mkdir()
        (plugin_dir / "manifest.yml").write_text(yaml.dump({
            'id': plugin_id,
            'name': plugin_id,
            'version': '1.0.0',
            'description': f'{plugin_id} boot fixture',
            'author': 'Test Author',
            'type': 'full-stack',
            'hooks': {
                'backend': [
                    {'hook': f'plugin.lifecycle.{event}', 'handler': f'hooks.{func}'}
                    for event, func in hooks.items()
                ]
            },
        }))
        body = [f"LOG = {str(log_file)!r}", ""]
        for event, func in hooks.items():
            body.append(f"def {func}(context):")
            body.append("    with open(LOG, 'a') as fh:")
            body.append(f"        fh.write('{plugin_id}:{event}\\n')")
            if event in failing:
                body.append(f"    raise RuntimeError('{plugin_id} {event} handler exploded')")
            body.append("    return context")
            body.append("")
        (plugin_dir / "hooks.py").write_text("\n".join(body))

    def events():
        return log_file.read_text().split() if log_file.exists() else []

    yield SimpleNamespace(
        registry=PluginRegistry(str(marketplace_dir), str(temp_dir / "local")),
        create_plugin=create_plugin,
        events=events,
    )

    shutil.rmtree(temp_dir)


def _db_plugin(plugin_id, enabled):
    return SimpleNamespace(id=plugin_id, enabled=enabled)


def test_boot_fires_at_startup_for_plugins_the_database_has_enabled(plugin_env):
    plugin_env.create_plugin("boot-plugin", {'boot': 'on_boot'})

    _sync_enabled_plugins(
        plugin_env.registry, FakePluginRepository([_db_plugin("boot-plugin", True)])
    )

    assert plugin_env.events() == ["boot-plugin:boot"]
    assert plugin_env.registry.get_plugin_state("boot-plugin") == PluginState.ENABLED


def test_enable_does_not_re_fire_at_startup(plugin_env):
    """A restart is not a disabled->enabled transition."""
    plugin_env.create_plugin("both-hooks", {'enable': 'on_enable', 'boot': 'on_boot'})

    _sync_enabled_plugins(
        plugin_env.registry, FakePluginRepository([_db_plugin("both-hooks", True)])
    )

    assert plugin_env.events() == ["both-hooks:boot"]


def test_boot_does_not_fire_for_plugins_disabled_in_the_database(plugin_env):
    plugin_env.create_plugin("on-plugin", {'boot': 'on_boot'})
    plugin_env.create_plugin("off-plugin", {'boot': 'on_boot'})

    _sync_enabled_plugins(plugin_env.registry, FakePluginRepository([
        _db_plugin("on-plugin", True),
        _db_plugin("off-plugin", False),
    ]))

    assert plugin_env.events() == ["on-plugin:boot"]


def test_one_plugins_failing_boot_does_not_abort_startup(plugin_env):
    plugin_env.create_plugin("aaa-broken", {'boot': 'on_boot'}, failing=('boot',))
    plugin_env.create_plugin("bbb-healthy", {'boot': 'on_boot'})

    _sync_enabled_plugins(plugin_env.registry, FakePluginRepository([
        _db_plugin("aaa-broken", True),
        _db_plugin("bbb-healthy", True),
    ]))

    assert sorted(plugin_env.events()) == ["aaa-broken:boot", "bbb-healthy:boot"]
    assert plugin_env.registry.get_plugin_state("bbb-healthy") == PluginState.ENABLED


def test_boot_runs_after_the_whole_enabled_set_is_registered(plugin_env):
    """A boot handler may look for its peers, so every plugin is enabled first."""
    plugin_env.create_plugin("first-plugin", {})
    plugin_env.create_plugin("second-plugin", {'boot': 'on_boot'})

    seen_states = []
    plugin_env.registry.hook_chain.register(
        "plugin.lifecycle.boot",
        "first-plugin",
        lambda ctx: seen_states.append(
            plugin_env.registry.get_plugin_state("second-plugin")
        ) or ctx,
    )

    _sync_enabled_plugins(plugin_env.registry, FakePluginRepository([
        _db_plugin("first-plugin", True),
        _db_plugin("second-plugin", True),
    ]))

    assert seen_states == [PluginState.ENABLED]
