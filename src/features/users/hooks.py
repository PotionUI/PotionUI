"""Hook points owned by the user domain."""

from src.platform.plugins.hooks import hooks_registry

USER_HOOKS = hooks_registry.declare(
    "user", "backend",
    "before_create", "after_create",
    "before_update", "after_update",
    "before_delete", "after_delete",
    specs={
        "before_create": {
            "description": "Fired before a user row is created (admin-initiated creation, not self-registration - see auth.before_register for that path). Can rewrite username/email or block creation.",
            "payload": {
                "username": {"type": "str", "description": "Requested username"},
                "email": {"type": "str", "description": "Requested email address"},
                "account_type": {"type": "str", "description": "'USER' or 'ADMIN' string, pre-validation"},
            },
            "mutable": ["username", "email", "blocked", "block_reason"],
            "use_when": [
                "Enforcing organization-specific username/email policy on admin-created accounts",
                "Blocking creation of accounts matching a denylist",
            ],
        },
        "after_create": {
            "description": "Fired after the user has been persisted.",
            "payload": {
                "user_id": {"type": "str", "description": "Newly created user's id"},
                "username": {"type": "str", "description": "Username used (post-hook value)"},
                "email": {"type": "str", "description": "Email used (post-hook value)"},
                "account_type": {"type": "str", "description": "'ADMIN' or 'USER' the account was created with"},
            },
            "mutable": [],
            "use_when": [
                "Notification-only: provisioning or auditing after admin-created accounts",
                "Assigning a newly created account to a default group/role based on account_type",
            ],
        },
        "before_update": {
            "description": "Fired before any field update (username/email/password/account_type) is applied. Can block the update; unlike other domains, individual fields are not exposed for rewriting - only the `updates` dict as a whole is visible.",
            "payload": {
                "user_id": {"type": "str", "description": "User being updated"},
                "updates": {"type": "dict", "description": "Fields to change, already validated - may contain 'username', 'email', 'password_hash', 'account_type' (an AccountType enum, not the raw string)"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Blocking specific field changes (e.g. account_type escalation) based on custom policy"],
        },
        "after_update": {
            "description": "Fired after the update has been persisted.",
            "payload": {
                "user_id": {"type": "str", "description": "Updated user's id"},
                "updates": {"type": "dict", "description": "Fields that were changed - same shape as before_update's `updates`"},
            },
            "mutable": [],
            "use_when": ["Notification-only: audit logging after user field changes"],
        },
        "before_delete": {
            "description": "Fired before a user is deleted. Can block the deletion.",
            "payload": {
                "user_id": {"type": "str", "description": "User to be deleted"},
                "username": {"type": "str", "description": "Username of user to be deleted"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": ["Blocking deletion of users owning resources that must be reassigned first"],
        },
        "after_delete": {
            "description": "Fired after the user has been removed from the repository.",
            "payload": {
                "user_id": {"type": "str", "description": "Deleted user's id"},
                "username": {"type": "str", "description": "Username at time of deletion"},
            },
            "mutable": [],
            "use_when": ["Notification-only: cascading cleanup of the deleted user's owned data"],
        },
    },
)
