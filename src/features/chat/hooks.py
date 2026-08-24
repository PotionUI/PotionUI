"""Hook points owned by the chat domain (sessions, messages, pre-actions, LLM responses)."""

from src.platform.plugins.hooks import hooks_registry

CHAT_SESSION_HOOKS = hooks_registry.declare(
    "chat.session", "backend",
    "before_create", "after_create",
    "before_accept", "after_accept",
    "before_reject", "after_reject",
    "before_delete", "after_delete",
)

CHAT_MESSAGE_HOOKS = hooks_registry.declare(
    "chat.message", "backend",
    "before_send", "after_send",
)

CHAT_PRE_ACTIONS_HOOKS = hooks_registry.declare(
    "chat.pre_actions", "backend",
    "register",
)

CHAT_RESPONSE_HOOKS = hooks_registry.declare(
    "chat.response", "backend",
    "transform",  # Transform content, extract actions (in ResponseProcessor)
    "before_generate", "after_save",  # LLM generation lifecycle (in ChatManager)
)
