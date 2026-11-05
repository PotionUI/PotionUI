"""Chat modes package."""

from src.platform.plugins.chat_modes import ChatMode, DuplicateChatModeError
from src.features.chat.modes.builtin import (
    CONVERSATION_MODE_ID,
    DEFAULT_TOOLS_SYSTEM_PROMPT_TEMPLATE,
    ELIXIRS_MODE_ID,
    GENERATION_MODE_ID,
    HISTORY_MODE_ID,
    MODELS_MODE_ID,
    PHRASEBOOK_MODE_ID,
    PROMPTS_MODE_ID,
    build_conversation_mode,
    build_elixirs_mode,
    build_generation_mode,
    build_history_mode,
    build_models_mode,
    build_phrasebook_mode,
    build_prompts_mode,
)
from src.features.chat.modes.registry import ChatModeRegistry

__all__ = [
    "ChatMode",
    "ChatModeRegistry",
    "DuplicateChatModeError",
    "CONVERSATION_MODE_ID",
    "DEFAULT_TOOLS_SYSTEM_PROMPT_TEMPLATE",
    "ELIXIRS_MODE_ID",
    "GENERATION_MODE_ID",
    "HISTORY_MODE_ID",
    "MODELS_MODE_ID",
    "PHRASEBOOK_MODE_ID",
    "PROMPTS_MODE_ID",
    "build_conversation_mode",
    "build_elixirs_mode",
    "build_generation_mode",
    "build_history_mode",
    "build_models_mode",
    "build_phrasebook_mode",
    "build_prompts_mode",
]
