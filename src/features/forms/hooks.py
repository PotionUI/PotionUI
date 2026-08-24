"""Hook points owned by the form domain."""

from src.platform.plugins.hooks import hooks_registry

FORM_HOOKS = hooks_registry.declare(
    "form", "backend",
    "before_get_options", "after_get_options",
    "before_validate", "after_validate",
)
