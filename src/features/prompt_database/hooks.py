"""Hook points owned by the aggregate Prompt library domain."""

from src.platform.plugins.hooks import hooks_registry

PROMPT_DATABASE_HOOKS = hooks_registry.declare(
    "prompt_database", "backend",
    "before_save", "after_save",
    specs={
        "before_save": {
            "description": "Before an independent Prompt aggregate is created or replaced.",
            "payload": {
                "prompt": {"type": "Prompt", "description": "Aggregate parent and ordered rich segments"},
                "segments": {"type": "list[RichSegment]", "description": "Complete ordered child collection"},
                "user_id": {"type": "str", "description": "Owning user"},
                "provider_id": {"type": "Optional[str]", "description": "Browsing source, if imported"},
            },
            "mutable": [],
            "use_when": ["Audit or validate detached Prompt saves"],
        },
        "after_save": {
            "description": "After an independent Prompt aggregate and its children commit.",
            "payload": {
                "prompt": {"type": "Prompt", "description": "Committed aggregate"},
                "segments": {"type": "list[RichSegment]", "description": "Committed ordered children"},
                "user_id": {"type": "str", "description": "Owning user"},
                "provider_id": {"type": "Optional[str]", "description": "Browsing source, if imported"},
            },
            "mutable": [],
            "use_when": ["Index, audit, or notify after a Prompt save"],
        },
    },
)
