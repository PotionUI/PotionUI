"""Hook points owned by the provider domain (marketplace provider plugins)."""

from src.platform.plugins.hooks import hooks_registry

PROVIDER_HOOKS = hooks_registry.declare(
    "provider", "backend",
    "register",  # Plugins register provider classes
    "settings.panel",  # Custom settings UI for providers -> attr settings_panel
    "before_settings_update", "after_settings_update",
    "before_initialize", "after_initialize",
    specs={
        "register": {
            "description": "Fired during provider discovery to let plugins register their MarketplaceProviderBase subclass.",
            "payload": {
                "providers": {"type": "Dict[str, type]", "description": "Map of provider ID -> provider class, seeded empty"},
            },
            "mutable": ["providers"],
            "use_when": [
                "Register a custom model marketplace provider (appears in admin -> Providers, used for model info/download lookups)",
            ],
            "example": (
                "# manifest.yml\n"
                "hooks:\n"
                "  backend:\n"
                "    - hook: \"provider.register\"\n"
                "      handler: \"hooks.provider_hooks.register_provider\"\n\n"
                "# hooks/provider_hooks.py\n"
                "def register_provider(context: HookContext) -> HookContext:\n"
                "    context.data[\"providers\"][\"civitai\"] = CivitaiProvider\n"
                "    return context\n"
            ),
        },
        "settings.panel": {
            "description": "Reserved for a custom settings UI hook point for providers. Declared but has no execute_hook/hook_chain.execute call site anywhere in the codebase - currently a dead hook.",
            "use_when": [
                "Not yet wired up; do not rely on this hook firing today",
            ],
        },
        "before_settings_update": {
            "description": "Fired before a provider's settings are persisted.",
            "payload": {
                "provider_id": {"type": "str", "description": "ID of the provider whose settings are being updated"},
                "settings": {"type": "dict", "description": "New settings dict submitted via the API"},
                "user_id": {"type": "str", "description": "User performing the update"},
            },
            "use_when": ["Validate or audit provider settings changes before they're saved (note: the controller does not currently apply any mutation back from this hook's result)"],
        },
        "after_settings_update": {
            "description": "Fired after a provider's settings have been persisted.",
            "payload": {
                "provider_id": {"type": "str", "description": "ID of the provider that was updated"},
                "settings": {"type": "dict", "description": "The settings dict that was applied"},
                "user_id": {"type": "str", "description": "User who performed the update"},
                "success": {"type": "bool", "description": "Whether the settings update succeeded"},
            },
            "use_when": ["React to provider settings changes, e.g. trigger a re-test of the connection"],
        },
        "before_initialize": {
            "description": "Fired before a provider is (re-)initialized with its current settings.",
            "payload": {
                "provider_id": {"type": "str", "description": "ID of the provider being initialized"},
                "user_id": {"type": "str", "description": "User who triggered initialization"},
            },
            "use_when": ["Prepare external state before a provider connects (note: result is not consumed to block initialization)"],
        },
        "after_initialize": {
            "description": "Fired after a provider (re-)initialization attempt completes.",
            "payload": {
                "provider_id": {"type": "str", "description": "ID of the provider that was initialized"},
                "user_id": {"type": "str", "description": "User who triggered initialization"},
                "success": {"type": "bool", "description": "Whether initialization succeeded"},
            },
            "use_when": ["Log or alert on provider initialization failures"],
        },
    },
)
