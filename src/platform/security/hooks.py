"""Hook points owned by the auth domain."""

from src.platform.plugins.hooks import hooks_registry

AUTH_HOOKS = hooks_registry.declare(
    "auth", "backend",
    "before_login", "after_login",
    "before_register", "after_register",
    specs={
        "before_login": {
            "description": "Fired before credentials are checked. Can block the login attempt (e.g. rate limiting, IP denylist).",
            "payload": {
                "username": {"type": "str", "description": "Username supplied in the login request"},
                "ip_address": {"type": "Optional[str]", "description": "Client IP address, if provided"},
                "user_agent": {"type": "Optional[str]", "description": "Client user agent string, if provided"},
            },
            "mutable": ["blocked", "block_reason"],
            "use_when": [
                "Rate-limit or deny login attempts by IP/user agent before password verification",
                "Log/audit login attempts prior to credential checks",
            ],
        },
        "after_login": {
            "description": "Fired after a successful login, once the access token has been issued.",
            "payload": {
                "user_id": {"type": "str", "description": "ID of the authenticated user"},
                "username": {"type": "str", "description": "Username of the authenticated user"},
                "ip_address": {"type": "Optional[str]", "description": "Client IP address, if provided"},
                "token": {"type": "str", "description": "Newly issued access token"},
            },
            "mutable": [],
            "use_when": [
                "Notification-only: audit logging, session analytics, or side-effect notifications on successful login",
            ],
        },
        "before_register": {
            "description": "Fired before a new user account is created. Can modify username/email or block registration.",
            "payload": {
                "username": {"type": "str", "description": "Requested username"},
                "email": {"type": "str", "description": "Requested email address"},
                "ip_address": {"type": "Optional[str]", "description": "Client IP address, if provided"},
                "user_agent": {"type": "Optional[str]", "description": "Client user agent string, if provided"},
            },
            "mutable": ["username", "email", "blocked", "block_reason"],
            "use_when": [
                "Enforce custom registration policy (e.g. email domain allowlist) before the uniqueness checks run",
                "Normalize/rewrite username or email prior to account creation",
            ],
        },
        "after_register": {
            "description": "Fired after a new user account and its initial access token have been created.",
            "payload": {
                "user_id": {"type": "str", "description": "ID of the newly created user"},
                "username": {"type": "str", "description": "Username of the newly created user"},
                "email": {"type": "str", "description": "Email of the newly created user"},
                "account_type": {"type": "str", "description": "'ADMIN' or 'USER' (first registered user becomes ADMIN)"},
                "token": {"type": "str", "description": "Access token issued for immediate login"},
            },
            "mutable": [],
            "use_when": [
                "Notification-only: welcome emails, provisioning default resources for a new user",
            ],
        },
    },
)
