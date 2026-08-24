"""
Plugin administration.

PluginManager drives the plugin machinery on behalf of the admin UI: it
enables, disables and configures the plugins the registry has discovered.

The machinery itself -- registry, loader, hook chain, manifest schema, router
manager -- is infrastructure and lives in `src.platform.plugins`.

PluginManager is not re-exported here; import it from `src.features.plugins.manager`
directly, to avoid a circular import.
"""
