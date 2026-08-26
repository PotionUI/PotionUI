"""
Plugin administration operations.

Post-Manager reference shape: no class holds these collaborators together.
Each operation is a module-level function that takes exactly the
collaborators it needs (repository, registry, and - only for the
enable/disable/delete lifecycle - the optional preset/pipe/recipe catalogues
that get rescanned afterward) as leading arguments, followed by the
operation's own parameters. Callers (``PluginController``, bootstrap startup
code, tests) hold the collaborators and pass them in; nothing here is stored
across calls.

Shape rule: one module per concern (`lifecycle`, `scan`, `settings`,
`frontend`), each re-exported here as the public surface - split a module
before it outgrows ~200 lines rather than let it absorb an unrelated concern.
Callers import from the package (`from src.features.plugins.operations import
enable_plugin`), never from a submodule directly.
"""
from src.features.plugins.operations.lifecycle import (
    enable_plugin,
    disable_plugin,
    delete_plugin,
)
from src.features.plugins.operations.scan import scan_plugins
from src.features.plugins.operations.settings import (
    PluginManifestUnavailableError,
    update_plugin_settings,
    encrypt_declared_secrets,
)
from src.features.plugins.operations.frontend import (
    get_active_quick_actions,
    get_active_sidebar_widgets,
    get_frontend_extensions,
    get_hooks_catalog,
)

__all__ = [
    "enable_plugin",
    "disable_plugin",
    "delete_plugin",
    "scan_plugins",
    "PluginManifestUnavailableError",
    "update_plugin_settings",
    "encrypt_declared_secrets",
    "get_active_quick_actions",
    "get_active_sidebar_widgets",
    "get_frontend_extensions",
    "get_hooks_catalog",
]
