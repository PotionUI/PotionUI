"""Hook points owned by the backend domain (marketplace backend plugins)."""

from src.platform.plugins.hooks import hooks_registry

BACKEND_HOOKS = hooks_registry.declare(
    "backend", "backend",
    "register",  # Plugins register backend types
    "before_create", "after_create",
    "before_update", "after_update",
    "before_delete", "after_delete",
    specs={
        "register": {
            "description": "Fired at backend-registry init to let plugins register their backend implementation and config classes.",
            "payload": {
                "backend_types": {"type": "Dict[str, type]", "description": "Map of backend type string -> Backend class, seeded empty"},
                "config_types": {"type": "Dict[str, type]", "description": "Map of backend type string -> BackendConfig class, seeded empty"},
            },
            "mutable": ["backend_types", "config_types"],
            "use_when": [
                "Register a custom generation backend (appears in admin -> Backends, selectable per preset)",
            ],
            "example": (
                "# manifest.yml\n"
                "hooks:\n"
                "  backend:\n"
                "    - hook: \"backend.register\"\n"
                "      handler: \"hooks.backend_hooks.register_backend\"\n\n"
                "# hooks/backend_hooks.py\n"
                "def register_backend(context: HookContext) -> HookContext:\n"
                "    context.data[\"backend_types\"][\"comfyui\"] = ComfyUIBackend\n"
                "    context.data[\"config_types\"][\"comfyui\"] = ComfyUIBackendConfig\n"
                "    return context\n"
            ),
        },
        "before_create": {
            "description": "Fired before a new backend configuration is validated and saved.",
            "payload": {
                "backend_data": {"type": "dict", "description": "Raw backend config dict submitted via the API (includes auto-generated 'id' if missing)"},
            },
            "mutable": ["backend_data"],
            "use_when": ["Inject default fields or credentials into a backend config before it's persisted"],
        },
        "after_create": {
            "description": "Fired after a new backend configuration has been saved and the registry refreshed.",
            "payload": {
                "backend_config": {"type": "dict", "description": "The saved BackendConfig, serialized via model_dump()"},
            },
            "use_when": ["Notify external systems that a new backend was added"],
        },
        "before_update": {
            "description": "Fired before an existing backend configuration is validated and updated.",
            "payload": {
                "backend_id": {"type": "str", "description": "ID of the backend being updated"},
                "backend_data": {"type": "dict", "description": "Raw updated backend config dict submitted via the API"},
            },
            "mutable": ["backend_data"],
            "use_when": ["Rewrite fields of a backend config before the update is applied"],
        },
        "after_update": {
            "description": "Fired after a backend configuration has been updated and the registry refreshed.",
            "payload": {
                "backend_id": {"type": "str", "description": "ID of the backend that was updated"},
                "backend_config": {"type": "dict", "description": "The updated BackendConfig, serialized via model_dump()"},
            },
            "use_when": ["React to configuration changes on a backend (e.g. re-test connectivity)"],
        },
        "before_delete": {
            "description": "Fired before a backend configuration is removed. Note: unlike other before_* hooks in this codebase, the controller does not currently check a 'blocked' flag from this hook's result.",
            "payload": {
                "backend_id": {"type": "str", "description": "ID of the backend to be deleted"},
            },
            "use_when": ["Clean up external resources tied to a backend before it's removed"],
        },
        "after_delete": {
            "description": "Fired after a backend configuration has been removed and the registry refreshed.",
            "payload": {
                "backend_id": {"type": "str", "description": "ID of the backend that was deleted"},
            },
            "use_when": ["Notify external systems that a backend was removed"],
        },
    },
)
