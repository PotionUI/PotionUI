"""Hook points owned by the notification domain."""

from src.platform.plugins.hooks import hooks_registry

NOTIFICATION_HOOKS = hooks_registry.declare(
    "notification", "backend",
    "before_create", "after_create",
    specs={
        "before_create": {
            "description": (
                "Fired before a notification is persisted/sent. Can rewrite "
                "`level`, `title`, `message`, or block it entirely."
            ),
            "payload": {
                "level": {"type": "str", "description": "'success' | 'error' | 'info' | 'warning'"},
                "title": {"type": "str", "description": "Notification title"},
                "message": {"type": "str", "description": "Notification body"},
                "category": {"type": "str", "description": "'generation' | 'system' | 'plugin' | free-form"},
                "user_id": {"type": "str", "description": "Target user id, or None for a broadcast"},
                "source": {"type": "str", "description": "'core' | 'frontend' | plugin id"},
            },
            "mutable": ["level", "title", "message", "blocked", "block_reason"],
            "use_when": [
                "Rewriting/localizing notification copy before it is persisted or sent",
                "Suppressing notifications matching a plugin-defined policy",
            ],
        },
        "after_create": {
            "description": "Fired once per persisted row, after it has been saved and pushed over WS.",
            "payload": {
                "notification_id": {"type": "str", "description": "Newly created notification's id"},
                "user_id": {"type": "str", "description": "Owning user id"},
                "category": {"type": "str", "description": "Notification category"},
                "level": {"type": "str", "description": "Notification level"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging, external forwarding (e.g. email/Slack)"],
        },
    },
)

NOTIFICATION_TYPE_HOOKS = hooks_registry.declare(
    "notification_type", "backend",
    "register",  # Plugins register NotificationTypeSpec entries
    specs={
        "register": {
            "description": (
                "Fired once at app startup to let plugins register additional "
                "notification types on the shared notification_type_registry."
            ),
            "payload": {
                "registry": {
                    "type": "NotificationTypeRegistry",
                    "description": "Shared registry; call registry.register(NotificationTypeSpec(...)) to add a type",
                },
            },
            "mutable": ["registry"],
            "use_when": [
                "Declaring a new notification `type` key so it shows up in the preferences UI "
                "and can be toggled per-user, before calling notify(..., type=...) with it",
            ],
            "example": (
                "# manifest.yml\n"
                "hooks:\n"
                "  backend:\n"
                "    - hook: \"notification_type.register\"\n"
                "      handler: \"hooks.notification_hooks.register_notification_types\"\n\n"
                "# hooks/notification_hooks.py\n"
                "def register_notification_types(context: HookContext) -> HookContext:\n"
                "    registry = context.data[\"registry\"]\n"
                "    registry.register(NotificationTypeSpec(key=\"myplugin.thing_happened\",\n"
                "        label=\"Thing happened\", category=\"plugin\"))\n"
                "    return context\n"
            ),
        },
    },
)
