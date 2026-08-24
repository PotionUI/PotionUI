"""Registry for chat modes."""

import logging
from typing import Dict, List, Optional

from src.features.chat.exceptions import UnknownChatModeException
from src.features.chat.reply_contract import REPLY_CONTRACT_PROMPT_BLOCK
from src.features.llm.tools.tool_conditionals import render_tool_conditionals
from src.platform.plugins.chat_modes import ChatMode, DuplicateChatModeError

logger = logging.getLogger(__name__)


class ChatModeRegistry:
    """Registry of available chat modes.

    The builtin 'generation' mode is registered at startup; plugins register
    additional modes when enabled and unregister them (by source) on disable.
    """

    def __init__(self):
        self._modes: Dict[str, ChatMode] = {}

    def register(self, mode: ChatMode) -> None:
        """Register a mode. Raises DuplicateChatModeError on id collision."""
        if mode.id in self._modes:
            raise DuplicateChatModeError(
                f"Chat mode '{mode.id}' is already registered "
                f"(by '{self._modes[mode.id].source}')"
            )
        self._modes[mode.id] = mode
        logger.debug(f"Registered chat mode: {mode.id} (source: {mode.source})")

    def unregister(self, mode_id: str) -> bool:
        """Unregister a mode by id. Returns True if it was found and removed."""
        if mode_id in self._modes:
            del self._modes[mode_id]
            logger.debug(f"Unregistered chat mode: {mode_id}")
            return True
        return False

    def unregister_source(self, source: str) -> int:
        """Unregister all modes registered by the given source (plugin id).

        Returns the number of modes removed.
        """
        to_remove = [m.id for m in self._modes.values() if m.source == source]
        for mode_id in to_remove:
            del self._modes[mode_id]
        if to_remove:
            logger.info(f"Unregistered chat modes {to_remove} for source '{source}'")
        return len(to_remove)

    def get(self, mode_id: str) -> Optional[ChatMode]:
        """Get a mode by id, or None."""
        return self._modes.get(mode_id)

    def get_all(self) -> List[ChatMode]:
        """Get all registered modes."""
        return list(self._modes.values())

    def require(self, mode_id: str) -> ChatMode:
        """Get a mode by id or raise UnknownChatModeException."""
        mode = self._modes.get(mode_id)
        if not mode:
            raise UnknownChatModeException(f"Unknown chat mode '{mode_id}'")
        return mode

    def resolve_system_prompt(
        self,
        mode: ChatMode,
        tool_hints_text: str,
        allowed_names: Optional[List[str]] = None,
    ) -> str:
        """Build the mode's system prompt for the session.

        Resolves any ``{{#if}}`` / ``{{#ifany}}`` tool-conditional blocks against
        ``allowed_names`` (so no rule references a tool the session can't call —
        available to plugin mode prompts too, since every mode's template flows
        through here), then substitutes ``{{TOOL_HINTS}}``.

        ``allowed_names=None`` keeps every conditional block (markers stripped),
        preserving the old behavior for callers that don't thread the allowed set.

        When ``mode.structured_reply`` is true (the default), the
        ``## improved`` / ``## questions`` reply-contract block is appended
        last, after tool-conditional resolution and ``{{TOOL_HINTS}}``
        substitution.
        """
        template = mode.resolve_prompt_template()
        template = render_tool_conditionals(template, allowed_names)
        resolved = template.replace("{{TOOL_HINTS}}", tool_hints_text or "")
        if mode.structured_reply:
            resolved += REPLY_CONTRACT_PROMPT_BLOCK
        return resolved
