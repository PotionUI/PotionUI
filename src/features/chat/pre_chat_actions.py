"""
Pre-chat actions system.

Allows plugins to register actions that run before LLM invocation.
Configuration is per LLM config via provider_options.pre_chat_actions.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

from src.features.chat.exceptions import PreChatActionError
from src.platform.plugins import PluginRegistry
from src.features.chat.hooks import CHAT_PRE_ACTIONS_HOOKS

logger = logging.getLogger(__name__)


@dataclass
class PreChatAction:
    """A registered pre-chat action."""
    id: str
    name: str
    description: str
    plugin_id: str
    execute: Callable[..., Coroutine[Any, Any, Dict[str, Any]]]
    default_enabled: bool = False
    blocking: bool = False
    category: str = "general"


@dataclass
class PreChatActionResult:
    """Result from executing a pre-chat action."""
    action_id: str
    success: bool
    duration_ms: float = 0.0
    message: str = ""
    error: Optional[str] = None


class PreChatActionRegistry:
    """Manages pre-chat actions registered by plugins."""

    def __init__(self, plugin_registry: PluginRegistry, llm_repository: Any):
        self._plugin_registry = plugin_registry
        self._llm_repository = llm_repository
        self._actions: Dict[str, PreChatAction] = {}

    def discover_actions(self) -> None:
        """Fire the CHAT_PRE_ACTIONS_REGISTER hook so plugins can register actions."""
        self._plugin_registry.execute_hook(
            CHAT_PRE_ACTIONS_HOOKS.register,
            initial_data={"registry": self},
        )
        logger.info(f"Discovered {len(self._actions)} pre-chat actions")

    def register_action(self, action: PreChatAction) -> None:
        """Register a pre-chat action."""
        self._actions[action.id] = action
        logger.debug(f"Registered pre-chat action: {action.id} ({action.name})")

    def unregister_action(self, action_id: str) -> bool:
        """Unregister a pre-chat action. Returns True if found and removed."""
        if action_id in self._actions:
            del self._actions[action_id]
            logger.debug(f"Unregistered pre-chat action: {action_id}")
            return True
        return False

    def get_all_actions(self) -> List[PreChatAction]:
        """Return all registered actions."""
        return list(self._actions.values())

    def get_enabled_actions(self, llm_config_id: str) -> List[PreChatAction]:
        """Return actions enabled for the given LLM config."""
        config = self._llm_repository.get_configuration(llm_config_id)
        if not config:
            return []

        provider_options = config.provider_options or {}
        action_settings = provider_options.get("pre_chat_actions", {})

        enabled = []
        for action in self._actions.values():
            explicitly_set = action.id in action_settings
            if explicitly_set:
                if action_settings[action.id]:
                    enabled.append(action)
            elif action.default_enabled:
                enabled.append(action)

        return enabled

    async def execute_actions(self, llm_config_id: str) -> List[PreChatActionResult]:
        """Execute all enabled pre-chat actions for the given LLM config.

        Runs actions concurrently. Non-blocking failures are logged as warnings.
        Blocking failures raise PreChatActionError.
        """
        actions = self.get_enabled_actions(llm_config_id)
        if not actions:
            return []

        logger.debug(f"Executing {len(actions)} pre-chat actions for LLM config {llm_config_id}")

        async def _run_action(action: PreChatAction) -> PreChatActionResult:
            start = time.monotonic()
            try:
                result = await action.execute()
                duration = (time.monotonic() - start) * 1000
                success = result.get("success", True)
                message = result.get("message", "")
                error = result.get("error") if not success else None

                if not success:
                    log_msg = f"Pre-chat action '{action.id}' failed: {error or message}"
                    if action.blocking:
                        logger.error(log_msg)
                    else:
                        logger.warning(log_msg)

                return PreChatActionResult(
                    action_id=action.id,
                    success=success,
                    duration_ms=duration,
                    message=message,
                    error=error,
                )
            except Exception as e:
                duration = (time.monotonic() - start) * 1000
                log_msg = f"Pre-chat action '{action.id}' raised exception: {e}"
                if action.blocking:
                    logger.error(log_msg)
                else:
                    logger.warning(log_msg)
                return PreChatActionResult(
                    action_id=action.id,
                    success=False,
                    duration_ms=duration,
                    error=str(e),
                )

        results = await asyncio.gather(*[_run_action(a) for a in actions])

        # Check for blocking failures
        for action, result in zip(actions, results):
            if action.blocking and not result.success:
                raise PreChatActionError(
                    f"Blocking pre-chat action '{action.id}' failed: {result.error}"
                )

        return list(results)
