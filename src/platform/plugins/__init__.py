"""
Plugin system for PotionUI.

The extension mechanism itself: the registry that tracks plugins and their
state, the loader that discovers them on disk, the hook chain they extend the
app through, and the manifest schema their declarations are validated against.

The operations that drive this machinery on behalf of the admin UI are a
feature and live in src.features.plugins.operations.

Usage:
    from src.platform.plugins import PluginRegistry

    registry = PluginRegistry()
    registry.discover_plugins()
    registry.enable_plugin("my-plugin-id")

    from src.platform.plugins import HookContext
    context = HookContext(
        hook_name="generation.before_start",
        plugin_id="system",
        data={"generation_id": "123"}
    )
    final_context, success = registry.execute_hook("generation.before_start", context)

Hooks are declared per-domain (next to the manager that owns them) via
`hooks_registry.declare(...)`, e.g. `src.features.generation.hooks.GENERATION_HOOKS`.
See `src.platform.plugins.hooks.hooks_registry` for the full catalog.
"""

from src.platform.plugins.registry import PluginRegistry, PluginState
from src.platform.plugins.loader import PluginLoader, PluginManifest
from src.platform.plugins.hooks import (
    HookChain,
    HookContext,
    HookResult,
    HookSpec,
    hooks_registry,
)

__all__ = [
    # Registry
    'PluginRegistry',
    'PluginState',

    # Loader
    'PluginLoader',
    'PluginManifest',

    # Hooks
    'HookChain',
    'HookContext',
    'HookResult',
    'HookSpec',
    'hooks_registry',
]
