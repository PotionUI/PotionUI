import pytest
import yaml

from src.features.notifications.hooks import NOTIFICATION_TYPE_HOOKS
from src.features.notifications.types import NotificationTypeRegistry
from src.platform.plugins.registry import PluginRegistry

HANDLER = """
from src.plugin_api.notifications import NotificationTypeSpec


def register_types(context):
    with open({log!r}, "a") as log:
        log.write("{plugin_id}\\n")
    context.data["registry"].register(NotificationTypeSpec(key="{plugin_id}.done", label="Done", category="plugin"))
    return context
"""


@pytest.fixture
def log(tmp_path):
    return tmp_path / "calls.log"


@pytest.fixture
def plugins(tmp_path, log):
    marketplace = tmp_path / "marketplace"
    (tmp_path / "local").mkdir()
    for plugin_id in ("boot-plugin", "hot-plugin"):
        plugin_dir = marketplace / plugin_id
        plugin_dir.mkdir(parents=True)
        manifest = {
            "id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "description": "Notification types fixture",
            "author": "Test Author",
            "type": "backend-only",
            "hooks": {"backend": [{"hook": NOTIFICATION_TYPE_HOOKS.register, "handler": "notification_hooks.register_types"}]},
        }
        (plugin_dir / "manifest.yml").write_text(yaml.dump(manifest))
        handler = HANDLER.replace("{log!r}", repr(str(log))).replace("{plugin_id}", plugin_id)
        (plugin_dir / "notification_hooks.py").write_text(handler)
    registry = PluginRegistry(str(marketplace), str(tmp_path / "local"))
    registry.discover_plugins()
    return registry


def startup(registry, types):
    registry.execute_hook(NOTIFICATION_TYPE_HOOKS.register, initial_data={"registry": types})


def calls(log):
    return log.read_text().split() if log.exists() else []


def test_a_plugin_enabled_after_startup_declares_its_types_at_once(plugins, log):
    types = NotificationTypeRegistry()
    assert plugins.enable_plugin("boot-plugin")
    startup(plugins, types)

    assert plugins.enable_plugin("hot-plugin")

    assert types.has("boot-plugin.done")
    assert types.get("hot-plugin.done").label == "Done"
    assert calls(log) == ["boot-plugin", "hot-plugin"]


def test_nothing_is_replayed_before_the_startup_hook_ran(plugins, log):
    assert plugins.enable_plugin("hot-plugin")

    assert calls(log) == []


def test_types_stay_declared_when_the_plugin_is_disabled_and_are_not_declared_twice(plugins, log):
    types = NotificationTypeRegistry()
    startup(plugins, types)
    assert plugins.enable_plugin("hot-plugin")

    assert plugins.disable_plugin("hot-plugin")
    assert types.has("hot-plugin.done")
    assert plugins.enable_plugin("hot-plugin")

    assert [spec.key for spec in types.all()] == ["hot-plugin.done"]
    assert calls(log) == ["hot-plugin"]


def test_a_plugin_declared_at_startup_is_not_declared_again_when_enabled_anew(plugins, log):
    types = NotificationTypeRegistry()
    assert plugins.enable_plugin("boot-plugin")
    startup(plugins, types)

    assert plugins.disable_plugin("boot-plugin")
    assert plugins.enable_plugin("boot-plugin")

    assert [spec.key for spec in types.all()] == ["boot-plugin.done"]
    assert calls(log) == ["boot-plugin"]
