"""
Plugin administration.

`operations.py` drives the plugin machinery on behalf of the admin UI:
module-level functions that enable, disable and configure the plugins the
registry has discovered. `PluginController` (`routes.py`) holds the
repository/registry/rescan collaborators and calls them.

The machinery itself -- registry, loader, hook chain, manifest schema, router
manager -- is infrastructure and lives in `src.platform.plugins`.
"""
