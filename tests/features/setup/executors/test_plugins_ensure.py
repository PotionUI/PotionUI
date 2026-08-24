"""`plugins.ensure` executor against a fake PluginRegistry (no real plugin
discovery/loading needed - just the surface the executor calls)."""

from types import SimpleNamespace

from src.features.setup.executors.base import StepContext
from src.features.setup.executors.plugins_ensure import PluginsEnsureExecutor
from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus
from src.platform.plugins.registry import PluginState


class FakePluginRegistry:
    def __init__(self, plugins=None, states=None, enable_results=None, errors=None):
        self.plugins = plugins or {}
        self.states = states or {}
        self.enable_results = enable_results or {}
        self.errors = errors or {}
        self.enable_calls = []

    def get_plugin(self, plugin_id):
        return self.plugins.get(plugin_id)

    def get_plugin_state(self, plugin_id):
        return self.states.get(plugin_id)

    def enable_plugin(self, plugin_id):
        self.enable_calls.append(plugin_id)
        return self.enable_results.get(plugin_id, True)

    def get_plugin_error(self, plugin_id):
        return self.errors.get(plugin_id)


def _context(plugin_ids):
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING, created_by="owner-1")
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native")
    step = RecipeStep(key="plugins.ensure", kind="plugins.ensure", title="Enable plugins", params={"plugin_ids": plugin_ids})
    return StepContext(run=run, recipe=recipe, step=step)


def test_no_plugin_ids_is_a_trivial_success():
    executor = PluginsEnsureExecutor(FakePluginRegistry())
    result = executor.execute(_context([]))
    assert result.success is True
    assert result.safe_output == {"enabled": [], "already_enabled": []}


def test_already_enabled_plugin_is_reported_not_re_enabled():
    registry = FakePluginRegistry(
        plugins={"downloader": SimpleNamespace(name="Model Downloader")},
        states={"downloader": PluginState.ENABLED},
    )
    executor = PluginsEnsureExecutor(registry)

    result = executor.execute(_context(["downloader"]))

    assert result.success is True
    assert result.safe_output == {"enabled": [], "already_enabled": ["downloader"]}
    assert registry.enable_calls == []  # never re-enabled


def test_disabled_plugin_gets_enabled():
    registry = FakePluginRegistry(
        plugins={"downloader": SimpleNamespace(name="Model Downloader")},
        states={"downloader": PluginState.DISABLED},
    )
    executor = PluginsEnsureExecutor(registry)

    result = executor.execute(_context(["downloader"]))

    assert result.success is True
    assert result.safe_output == {"enabled": ["downloader"], "already_enabled": []}
    assert registry.enable_calls == ["downloader"]


def test_missing_plugin_fails_with_human_readable_detail():
    executor = PluginsEnsureExecutor(FakePluginRegistry())

    result = executor.execute(_context(["downloader"]))

    assert result.success is False
    assert result.error_code == "PLUGIN_ENSURE_FAILED"
    assert "not installed" in result.safe_error_detail
    assert result.suggested_repair is not None


def test_enable_failure_surfaces_plugin_error():
    registry = FakePluginRegistry(
        plugins={"downloader": SimpleNamespace(name="Model Downloader")},
        states={"downloader": PluginState.DISABLED},
        enable_results={"downloader": False},
        errors={"downloader": "missing dependency: ffmpeg"},
    )
    executor = PluginsEnsureExecutor(registry)

    result = executor.execute(_context(["downloader"]))

    assert result.success is False
    assert "missing dependency: ffmpeg" in result.safe_error_detail
