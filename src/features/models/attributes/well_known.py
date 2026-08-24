class WellKnownModelAttribute:
    """Attribute keys core seeds as system definitions, so callers reference the
    constant instead of retyping the string literal (re-exported through
    `src.plugin_api` for plugin authors, mirroring `WellKnownField` pre-v2)."""

    TRIGGERS = "triggers"
    LORA_STRENGTH = "strength"
