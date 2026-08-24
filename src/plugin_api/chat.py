"""Extending the chat assistant.

A tool is a capability you hand the assistant. Subclass `BaseTool`, declare its
name and JSON schema, and return a `ToolResult` from `execute`. The `ToolContext`
it receives carries the calling user and the session the tool was invoked from.
`ToolSource` marks where a tool came from, so a plugin's tools can be withdrawn
again when it is disabled.

A `PreChatAction` runs before the model sees the conversation - use it to put
something in front of the user first (a choice to make, a resource to pick).
"""

from src.features.chat.pre_chat_actions import PreChatAction
from src.features.llm.tools.base import BaseTool, ToolContext, ToolResult, ToolSource

__all__ = [
    "BaseTool",
    "PreChatAction",
    "ToolContext",
    "ToolResult",
    "ToolSource",
]
