"""Hook points owned by the phrasebook domain."""

from src.platform.plugins.hooks import hooks_registry

PHRASEBOOK_HOOKS = hooks_registry.declare(
    "phrasebook", "backend",
    "before_import", "after_import",
    "before_create", "after_create",
    "before_update", "after_update",
    "before_delete", "after_delete",
)
