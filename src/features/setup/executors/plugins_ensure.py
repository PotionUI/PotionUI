"""`plugins.ensure` - enable the bundled plugins a recipe needs."""

from __future__ import annotations

from typing import List

from src.features.setup.executors.base import StepContext, StepResult
from src.platform.plugins import PluginRegistry
from src.platform.plugins.registry import PluginState


class PluginsEnsureExecutor:
    """Ensures every plugin id in `step.params['plugin_ids']` is enabled.

    Only ever enables plugins already discovered on disk (bundled/marketplace/
    local plugin directories) - it never installs new plugin code. A plugin
    genuinely missing from the installation is reported as a failure with a
    plain-language pointer at Administration -> Plugins.
    """

    def __init__(self, plugin_registry: PluginRegistry):
        self.plugin_registry = plugin_registry

    def execute(self, context: StepContext) -> StepResult:
        plugin_ids: List[str] = list(context.step.params.get("plugin_ids") or [])
        if not plugin_ids:
            return StepResult.ok({"enabled": [], "already_enabled": []})

        enabled: List[str] = []
        already_enabled: List[str] = []
        problems: List[str] = []

        for plugin_id in plugin_ids:
            manifest = self.plugin_registry.get_plugin(plugin_id)
            if manifest is None:
                problems.append(f"'{plugin_id}' is not installed on this instance")
                continue

            if self.plugin_registry.get_plugin_state(plugin_id) == PluginState.ENABLED:
                already_enabled.append(plugin_id)
                continue

            if self.plugin_registry.enable_plugin(plugin_id):
                enabled.append(plugin_id)
            else:
                error = self.plugin_registry.get_plugin_error(plugin_id) or "it failed to start"
                problems.append(f"'{manifest.name}' could not be enabled ({error})")

        if problems:
            return StepResult.fail(
                "PLUGIN_ENSURE_FAILED",
                "Some required plugins aren't ready: " + "; ".join(problems) + ".",
                suggested_repair="Open Administration -> Plugins and enable or reinstall the plugin(s) listed above.",
            )

        return StepResult.ok({"enabled": enabled, "already_enabled": already_enabled})
