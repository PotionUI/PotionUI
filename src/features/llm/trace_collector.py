"""Wire-exact LLM call tracing for the admin session-debug viewer.

The provider clients (``clients/openai.py``, ``clients/ollama.py``) are the
only place the fully assembled request array exists, so they call into
``record()`` right after building the payload and getting the response. They
stay dumb: ``record()`` is a no-op unless a chat turn has ``activate()``d a
trace context here (mirrors the ``_cache_owner`` ContextVar pattern in
``src.platform.runtime.model_lifecycle.manager``).

``src.features.chat.conversation.ConversationRunner`` activates the context
per turn (session_id/user_id/purpose); ``ChatTitleGenerator`` activates its
own nested context with purpose='title' around the title call. The actual
persistence (and the ``chat_llm_call_tracing`` setting check) lives in
``ChatCallTraceRecorder`` (``trace_recorder.py``), installed via
``set_recorder()`` during bootstrap.
"""

import contextvars
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class _TraceContext:
    session_id: str
    user_id: Optional[str]
    purpose: str
    _iteration: List[int] = field(default_factory=lambda: [0])

    def next_iteration(self) -> int:
        self._iteration[0] += 1
        return self._iteration[0]


_trace_context: contextvars.ContextVar[Optional[_TraceContext]] = contextvars.ContextVar(
    "chat_llm_trace_context", default=None
)

# Installed once at bootstrap (see src/bootstrap/container.py) so clients can
# record without importing the chat feature or the repository directly.
_recorder: Optional[Any] = None


def set_recorder(recorder: Any) -> None:
    """Install the process-wide recorder. Called once from build_container()."""
    global _recorder
    _recorder = recorder


@contextmanager
def activate(session_id: str, user_id: Optional[str], purpose: str = "chat") -> Iterator[None]:
    """Activate trace recording for the current async context.

    Safe to nest (e.g. title generation activating purpose='title' inside a
    turn already activated with purpose='chat') — the inner context wins for
    calls made within it and is restored to the outer one on exit.
    """
    token = _trace_context.set(_TraceContext(session_id=session_id, user_id=user_id, purpose=purpose))
    try:
        yield
    finally:
        _trace_context.reset(token)


def is_active() -> bool:
    return _trace_context.get() is not None


def record(
    *,
    provider: str,
    model: str,
    request_system: Optional[str],
    request_messages: List[Dict[str, Any]],
    request_params: Dict[str, Any],
    request_tools: Optional[Any] = None,
    response_text: Optional[str] = None,
    response_tool_calls: Optional[Any] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    duration_ms: int = 0,
) -> None:
    """Record one wire-exact LLM call. No-op with no active context or recorder.

    Never raises — a tracing failure must not break the chat turn it observed.
    """
    ctx = _trace_context.get()
    if ctx is None or _recorder is None:
        return
    try:
        _recorder.record(
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            purpose=ctx.purpose,
            iteration=ctx.next_iteration(),
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
    except Exception:
        logger.exception("Failed to record chat LLM call trace")
