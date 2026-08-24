"""Hook points owned by the session domain."""

from src.platform.plugins.hooks import hooks_registry

SESSION_HOOKS = hooks_registry.declare(
    "session", "backend",
    "before_create", "after_create",
    "before_update", "after_update",
    "before_delete", "after_delete",
    specs={
        "before_create": {
            "description": "Fired before a new session row is built. Can rewrite `data` or block creation.",
            "payload": {
                "preset_id": {"type": "str", "description": "Preset the session belongs to"},
                "name": {"type": "str", "description": "Session name"},
                "data": {"type": "dict", "description": "Form/session data to persist"},
                "mode": {"type": "Optional[str]", "description": "Save mode, used for merge logic on update paths"},
                "user_id": {"type": "str", "description": "Owning user's id"},
            },
            "mutable": ["data", "blocked", "block_reason"],
            "use_when": [
                "Validating or transforming session data before it's persisted",
                "Blocking session creation for a user/preset combination (e.g. quota enforcement)",
            ],
        },
        "after_create": {
            "description": "Fired after the session row has been persisted.",
            "payload": {
                "session_id": {"type": "str", "description": "Newly created session's id"},
                "preset_id": {"type": "str", "description": "Preset the session belongs to"},
                "name": {"type": "str", "description": "Session name"},
                "user_id": {"type": "str", "description": "Owning user's id"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging or side effects after a session is saved"],
        },
        "before_update": {
            "description": "Fired before an existing session is updated - covers both the save-by-name merge path and the update-by-id path (payload keys differ slightly: the by-id path also includes `old_name`/`new_name`).",
            "payload": {
                "session_id": {"type": "str", "description": "Session being updated"},
                "preset_id": {"type": "str", "description": "Preset the session belongs to"},
                "name": {"type": "str", "description": "Session name (save-by-name path only)"},
                "old_name": {"type": "str", "description": "Current session name (update-by-id path only)"},
                "new_name": {"type": "str", "description": "Requested new session name (update-by-id path only)"},
                "old_data": {"type": "dict", "description": "Session data before the update"},
                "new_data": {"type": "dict", "description": "Requested new session data, pre-merge"},
                "mode": {"type": "Optional[str]", "description": "Merge mode applied to old_data/new_data"},
                "user_id": {"type": "str", "description": "Owning user's id"},
            },
            "mutable": ["new_data", "blocked", "block_reason"],
            "use_when": [
                "Rewriting or validating the incoming data before it's merged into the session",
                "Blocking updates to sessions matching some policy",
            ],
        },
        "after_update": {
            "description": "Fired after the session update has been persisted.",
            "payload": {
                "session_id": {"type": "str", "description": "Updated session's id"},
                "preset_id": {"type": "str", "description": "Preset the session belongs to"},
                "name": {"type": "str", "description": "Session's current name"},
                "user_id": {"type": "str", "description": "Owning user's id"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after a session update"],
        },
        "before_delete": {
            "description": "Fired before a session is deleted. Can block the deletion.",
            "payload": {
                "session_id": {"type": "str", "description": "Session to be deleted"},
                "preset_id": {"type": "str", "description": "Preset the session belongs to"},
                "name": {"type": "str", "description": "Session name"},
                "user_id": {"type": "str", "description": "Owning user's id"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Blocking deletion of protected/pinned sessions"],
        },
        "after_delete": {
            "description": "Fired after the session has been removed from the repository.",
            "payload": {
                "session_id": {"type": "str", "description": "Deleted session's id"},
                "preset_id": {"type": "str", "description": "Preset the session belonged to"},
                "name": {"type": "str", "description": "Session's name at time of deletion"},
                "user_id": {"type": "str", "description": "Owning user's id"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging or cleanup of related plugin state after deletion"],
        },
    },
)
