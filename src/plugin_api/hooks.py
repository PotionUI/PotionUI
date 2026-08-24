"""Hooking into the application.

Hooks are how a plugin reacts to things happening elsewhere. Declare them in the
manifest and write a handler that takes a `HookContext` and returns it: whatever
you put in `context.data` is what the next handler - and the application - sees.
`hooks_registry` is the catalog of hooks available to hook into.

`get_container()` reaches the application's wired-up managers (the generation
orchestrator, the model lifecycle manager, and so on). Call it inside the
function that needs it, not at import time: the container does not exist while
your module is being imported.
"""

from src.features.generation.exceptions import GenerationNotFoundException
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import HookContext, HookResult, HookSpec, hooks_registry
from src.platform.plugins.runtime_registries import (
    get_container,
    get_global_plugin_registry,
    get_global_tool_registry,
)
from src.platform.runtime.model_lifecycle.manager import ModelLifecycleManager

__all__ = [
    "GenerationNotFoundException",
    "HookContext",
    "HookResult",
    "HookSpec",
    "ModelLifecycleManager",
    "PluginRegistry",
    "get_container",
    "get_global_plugin_registry",
    "get_global_tool_registry",
    "hooks_registry",
]
