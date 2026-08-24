"""Hook points fired by the plugin manager itself around enable/disable."""

from src.platform.plugins.hooks import hooks_registry

PLUGIN_LIFECYCLE_HOOKS = hooks_registry.declare(
    "plugin.lifecycle", "backend",
    "enable", "boot", "disable",
    specs={
        "enable": {
            "description": "Fired after a plugin has been enabled in the database and registry (its own hooks are already registered by this point), on the disabled->enabled transition ONLY - never again on a restart of an already-enabled plugin. Per-process initialization belongs in 'plugin.lifecycle.boot', which fires on both paths. The hook chain's result is not consulted - this cannot block enabling.",
            "payload": {
                "plugin_id": {"type": "str", "description": "Identifier of the plugin that was just enabled"},
            },
            "mutable": [],
            "use_when": [
                "Reacting to another plugin becoming available - e.g. wiring up cross-plugin integration once both sides are enabled",
                "Notification-only: audit logging of plugin enable events",
            ],
            "example": (
                "def handler(ctx):\n"
                "    if ctx.get('plugin_id') == 'some-other-plugin':\n"
                "        register_download_integration()\n"
                "    return ctx"
            ),
        },
        "boot": {
            "description": "Fired once per process for each enabled plugin: at startup for every plugin the database has enabled, and immediately after 'plugin.lifecycle.enable' when a plugin is enabled at runtime. Unlike the other two lifecycle hooks this is dispatched only to the subject plugin's own handler, so 'plugin_id' is always the plugin being booted. A handler that raises is logged and skipped - it cannot abort startup or another plugin's boot. The hook chain's result is not consulted.",
            "payload": {
                "plugin_id": {"type": "str", "description": "Identifier of the plugin being booted - always the handler's own plugin"},
            },
            "mutable": [],
            "use_when": [
                "Per-process initialization that must survive a restart: creating the plugin's own tables, warming a cache, starting a background worker",
                "Anything you would have put in 'enable' expecting it to run on every boot",
            ],
            "example": (
                "def handler(ctx):\n"
                "    MyPluginTables().create_all()\n"
                "    return ctx"
            ),
        },
        "disable": {
            "description": "Fired before a plugin's hooks are unregistered (called right after the plugin lookup, prior to actual unregistration). Notification-only, cannot block disabling.",
            "payload": {
                "plugin_id": {"type": "str", "description": "Identifier of the plugin being disabled"},
            },
            "mutable": [],
            "use_when": ["Cleaning up cross-plugin state that depended on the plugin being disabled"],
        },
    },
)
