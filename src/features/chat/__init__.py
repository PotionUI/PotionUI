"""
Chat module for PotionUI.

This module provides chat functionality including:
- ChatManager: Orchestrates chat session and message operations
- ResponseProcessor: Processes LLM responses with plugin hooks
- Domain exceptions: ChatException and specific subtypes
"""

from src.features.chat.manager import ChatManager
from src.features.chat.response_processor import ResponseProcessor
from src.features.chat.pre_chat_actions import PreChatActionManager, PreChatAction, PreChatActionResult
from src.features.chat.exceptions import (
    ChatException,
    SessionNotFoundException,
    AccessDeniedException,
    SessionClosedException,
    InvalidLLMConfigException,
    MessageCreationFailedException,
    SessionCreationFailedException,
    PreChatActionError,
)

__all__ = [
    # Main classes
    "ChatManager",
    "ResponseProcessor",
    "PreChatActionManager",
    "PreChatAction",
    "PreChatActionResult",

    # Exceptions
    "ChatException",
    "SessionNotFoundException",
    "AccessDeniedException",
    "SessionClosedException",
    "InvalidLLMConfigException",
    "MessageCreationFailedException",
    "SessionCreationFailedException",
    "PreChatActionError",
]
