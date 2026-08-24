"""Hook points owned by the template processing domain."""

from src.platform.plugins.hooks import hooks_registry

TEMPLATE_HOOKS = hooks_registry.declare(
    "template", "backend",
    "before_process", "after_process",
    "resolve_path",  # Allows plugins to add custom path types
)
