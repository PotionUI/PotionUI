"""Settings-gated recorder that backs ``src.features.llm.trace_collector``.

Installed onto the collector via ``trace_collector.set_recorder()`` during
``build_container()``. Kept separate from the collector so the collector
module (imported by the provider clients) stays free of the settings/DB
dependency — the clients only ever see ``trace_collector.record()``.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from src.features.llm.trace_repository import ChatCallTraceRepository
from src.platform.settings.settings import SettingsManager

logger = logging.getLogger(__name__)

# Prune is triggered opportunistically off the write path, throttled to at
# most once per process per interval so it never adds per-insert query cost.
PRUNE_THROTTLE_SECONDS = 3600.0


class ChatCallTraceRecorder:
    """Checks the ``chat_llm_call_tracing`` setting, then persists a call trace."""

    def __init__(
        self,
        repository: ChatCallTraceRepository,
        settings_manager: SettingsManager,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._repository = repository
        self._settings_manager = settings_manager
        self._clock = clock
        self._last_prune_at: Optional[float] = None

    def _enabled(self) -> bool:
        return bool(self._settings_manager.get_setting("chat_llm_call_tracing", True))

    def _maybe_prune(self) -> None:
        now = self._clock()
        if self._last_prune_at is not None and now - self._last_prune_at < PRUNE_THROTTLE_SECONDS:
            return
        self._last_prune_at = now
        self._repository.prune_older_than()

    def record(
        self,
        *,
        session_id: str,
        user_id: Optional[str],
        purpose: str,
        iteration: int,
        provider: str,
        model: str,
        request_system: Optional[str],
        request_messages: List[Dict[str, Any]],
        request_params: Dict[str, Any],
        request_tools: Optional[Any],
        response_text: Optional[str],
        response_tool_calls: Optional[Any],
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
        duration_ms: int,
    ) -> None:
        if not self._enabled():
            return
        self._repository.create(
            session_id=session_id,
            user_id=user_id,
            purpose=purpose,
            iteration=iteration,
            provider=provider,
            model=model,
            request_system=request_system,
            request_messages=request_messages,
            request_params=request_params,
            request_tools=request_tools,
            response_text=response_text,
            response_tool_calls=response_tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
        )
        self._maybe_prune()
