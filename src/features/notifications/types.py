"""
Notification type registry.

Mirrors `src.features.generation.output_types.OutputTypeRegistry`: a single
declaration point for each kind of notification a producer (core or plugin)
can raise. Each type has a stable string `key` (e.g. "generation.completed"),
used both by producers (`NotificationManager.notify(..., type=...)`) and by
per-user preferences (`{"types": {"<key>": bool}}`) to decide whether a
notification of that type should be delivered to a given user at all.

Plugins extend this via the `notification_type.register` hook (see
`src/features/notifications/hooks.py`), fired once at app startup next to the
`output_type.register` hook in `build_container()`.
"""

from dataclasses import dataclass
from typing import List


class DuplicateNotificationTypeError(ValueError):
    """Raised when registering a NotificationTypeSpec whose key already exists."""


@dataclass(frozen=True)
class NotificationTypeSpec:
    """Declaration for a single notification type."""
    key: str
    label: str
    description: str = ""
    category: str = "system"
    default_enabled: bool = True
    admin_only: bool = False


class NotificationTypeRegistry:
    """Registry mapping notification type keys to their NotificationTypeSpec."""

    def __init__(self):
        self._by_key: dict[str, NotificationTypeSpec] = {}

    def register(self, spec: NotificationTypeSpec) -> None:
        """Register a new NotificationTypeSpec. Raises on duplicate key."""
        if spec.key in self._by_key:
            raise DuplicateNotificationTypeError(
                f"Notification type key already registered: '{spec.key}'"
            )
        self._by_key[spec.key] = spec

    def get(self, key: str) -> NotificationTypeSpec | None:
        """Look up a spec by key, or None if unregistered."""
        return self._by_key.get(key)

    def has(self, key: str) -> bool:
        """True if a spec is registered under this key."""
        return key in self._by_key

    def all(self) -> List[NotificationTypeSpec]:
        """Return all registered specs."""
        return list(self._by_key.values())


# Module-level singleton used across the application and by plugins.
notification_type_registry = NotificationTypeRegistry()

notification_type_registry.register(NotificationTypeSpec(
    key="generation.completed",
    label="Generation completed",
    description="A generation finished successfully.",
    category="generation",
))
notification_type_registry.register(NotificationTypeSpec(
    key="generation.failed",
    label="Generation failed",
    description="A generation failed with an error.",
    category="generation",
))
notification_type_registry.register(NotificationTypeSpec(
    key="system.plugins",
    label="Plugin lifecycle events",
    description="A plugin was installed/enabled/disabled, or a lifecycle operation failed.",
    category="system",
    admin_only=True,
))
notification_type_registry.register(NotificationTypeSpec(
    key="inspiration.comment",
    label="Inspiration comments",
    description="Someone commented on one of your published inspirations.",
    category="inspirations",
))
