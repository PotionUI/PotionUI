"""Hook points owned by the LLM domain (configs, generation, tools)."""

from src.platform.plugins.hooks import hooks_registry

LLM_CONFIG_HOOKS = hooks_registry.declare(
    "llm.config", "backend",
    "before_create", "after_create",
    "before_update", "after_update",
    "before_delete", "after_delete",
)

LLM_HOOKS = hooks_registry.declare(
    "llm", "backend",
    "before_generate", "after_generate",
)

# Note: "llm.tool.before_execute"/"after_execute" existed in the previous hook
# enum with zero call sites and no manifest references - dropped rather than re-declared.
