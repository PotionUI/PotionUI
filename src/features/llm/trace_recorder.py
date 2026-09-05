"""Settings-gated recorder that backs ``src.features.llm.trace_collector``.

Installed onto the collector via ``trace_collector.set_recorder()`` during
``build_container()``. Kept separate from the collector so the collector
module (imported by the provider clients) stays free of the settings/DB
dependency — the clients only ever see ``trace_collector.record()``.

``record()`` is called from the provider clients the moment a completion
lands, i.e. inside the chat turn the user is waiting on. It therefore only
snapshots and enqueues; a single worker thread owns the INSERT and the
retention prune. Clients call it from async code, so the writer is a thread
rather than a task: an ``asyncio`` writer would need a loop handle threaded
through eight call sites and would still serialize the blocking sqlite write
onto the loop.
"""

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.features.llm.trace_repository import ChatCallTraceRepository
from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)

# Prune is triggered opportunistically from the writer thread, throttled to at
# most once per process per interval so it never adds per-insert query cost.
PRUNE_THROTTLE_SECONDS = 3600.0

TRACE_QUEUE_MAXSIZE = 256

# Per-field ceiling on the JSON a single trace contributes, in characters
# (a close enough proxy for bytes on JSON text, and one full encode cheaper).
# A history carrying an inline base64 image is megabytes on its own, and a
# full queue of those would be gigabytes resident.
MAX_TRACE_FIELD_CHARS = 64 * 1024

# Ceiling on any one string nested inside a field. Shrinking these first keeps
# an oversized history readable (its structure and short messages survive)
# instead of discarding the field wholesale.
MAX_TRACE_STRING_CHARS = 8 * 1024

_STOP = object()


def _truncation_marker(original_chars: int) -> str:
    return f"\n…[truncated: {original_chars} chars]"


def _cap_text(value: Optional[str]) -> Tuple[Optional[str], bool]:
    if value is None or len(value) <= MAX_TRACE_FIELD_CHARS:
        return value, False
    return value[:MAX_TRACE_FIELD_CHARS] + _truncation_marker(len(value)), True


def _shrink_strings(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= MAX_TRACE_STRING_CHARS:
            return value
        return value[:MAX_TRACE_STRING_CHARS] + _truncation_marker(len(value))
    if isinstance(value, dict):
        return {key: _shrink_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shrink_strings(item) for item in value]
    return value


def _keep_tail(items: List[Any]) -> List[Any]:
    """Keep as many trailing entries as fit the field budget, oldest dropped.

    The tail of a message array is the part a debug trace is read for — the
    turn that produced this call — so the head is what gets sacrificed.
    """
    kept: List[Any] = []
    budget = MAX_TRACE_FIELD_CHARS
    for item in reversed(items):
        text = json.dumps(item, default=str)
        if len(text) > budget:
            break
        budget -= len(text)
        kept.append(item)
    kept.reverse()
    dropped = len(items) - len(kept)
    return [{"role": "system", "content": f"[{dropped} earlier entries dropped: trace field over {MAX_TRACE_FIELD_CHARS} chars]"}] + kept


def _cap_json(value: Any) -> Tuple[Optional[str], bool]:
    """Serialize a structured field, bounded, returning (json_text, truncated).

    The JSON text *is* the snapshot: it is immutable, so a caller mutating the
    history afterwards cannot change what gets persisted, and its length is the
    exact memory a queued record holds. The writer thread decodes it again for
    the repository, which owns the encoding of what it stores.
    """
    if value is None:
        return None, False
    text = json.dumps(value, default=str)
    if len(text) <= MAX_TRACE_FIELD_CHARS:
        return text, False

    shrunk = _shrink_strings(value)
    text = json.dumps(shrunk, default=str)
    if len(text) <= MAX_TRACE_FIELD_CHARS:
        return text, True

    if isinstance(shrunk, list):
        return json.dumps(_keep_tail(shrunk), default=str), True
    return json.dumps({"truncated": _truncation_marker(len(text))}), True


@dataclass(frozen=True)
class _QueuedTrace:
    """One trace, fully detached from the caller's objects."""

    session_id: str
    user_id: Optional[str]
    purpose: str
    iteration: int
    provider: str
    model: str
    request_system: Optional[str]
    request_messages: Optional[str]
    request_params: Optional[str]
    request_tools: Optional[str]
    response_text: Optional[str]
    response_tool_calls: Optional[str]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    duration_ms: int


class ChatCallTraceRecorder:
    """Checks the ``chat_llm_call_tracing`` setting, then queues a call trace."""

    def __init__(
        self,
        repository: ChatCallTraceRepository,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.monotonic,
        queue_maxsize: int = TRACE_QUEUE_MAXSIZE,
    ):
        self._repository = repository
        self._settings = settings
        self._clock = clock
        self._last_prune_at: Optional[float] = None
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=queue_maxsize)
        self._worker: Optional[threading.Thread] = None
        self._worker_lock = threading.Lock()
        self._stopped = False
        self._discarding = threading.Event()
        self._counters_lock = threading.Lock()
        self._counters = {"written": 0, "dropped": 0, "truncated": 0, "failed": 0}

    def stats(self) -> Dict[str, int]:
        with self._counters_lock:
            counters = dict(self._counters)
        counters["queued"] = self._queue.qsize()
        return counters

    def _bump(self, counter: str) -> None:
        with self._counters_lock:
            self._counters[counter] += 1

    def _enabled(self) -> bool:
        return bool(self._settings.get_setting("chat_llm_call_tracing", True))

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
        if not self._enabled() or self._stopped:
            return

        messages_json, messages_cut = _cap_json(request_messages)
        params_json, params_cut = _cap_json(request_params)
        tools_json, tools_cut = _cap_json(request_tools)
        tool_calls_json, tool_calls_cut = _cap_json(response_tool_calls)
        system_text, system_cut = _cap_text(request_system)
        response_text_capped, response_cut = _cap_text(response_text)

        item = _QueuedTrace(
            session_id=session_id,
            user_id=user_id,
            purpose=purpose,
            iteration=iteration,
            provider=provider,
            model=model,
            request_system=system_text,
            request_messages=messages_json,
            request_params=params_json,
            request_tools=tools_json,
            response_text=response_text_capped,
            response_tool_calls=tool_calls_json,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
        )

        self._ensure_worker()
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # Drop the newest, never the oldest and never block: the queue is
            # full because the DB is slower than the chat is producing, and the
            # traces already in it are the earlier calls of the same turns —
            # the ones that explain the later ones. Blocking here would put the
            # write back on the response path, which is the whole point.
            self._bump("dropped")
            return

        if messages_cut or params_cut or tools_cut or tool_calls_cut or system_cut or response_cut:
            self._bump("truncated")

    def _ensure_worker(self) -> None:
        if self._worker is not None:
            return
        with self._worker_lock:
            if self._worker is not None or self._stopped:
                return
            self._worker = threading.Thread(
                target=self._run, name="chat-trace-writer", daemon=True,
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            if self._discarding.is_set():
                continue
            try:
                self._write(item)
            except Exception:
                self._bump("failed")
                logger.exception("Failed to persist chat LLM call trace")
            try:
                self._maybe_prune()
            except Exception:
                # A failing prune must not take the writer down with it —
                # every later trace would be silently lost.
                logger.exception("Failed to prune chat LLM call traces")

    def _write(self, item: _QueuedTrace) -> None:
        self._repository.create(
            session_id=item.session_id,
            user_id=item.user_id,
            purpose=item.purpose,
            iteration=item.iteration,
            provider=item.provider,
            model=item.model,
            request_system=item.request_system,
            request_messages=json.loads(item.request_messages) if item.request_messages else [],
            request_params=json.loads(item.request_params) if item.request_params else {},
            request_tools=json.loads(item.request_tools) if item.request_tools else None,
            response_text=item.response_text,
            response_tool_calls=json.loads(item.response_tool_calls) if item.response_tool_calls else None,
            prompt_tokens=item.prompt_tokens,
            completion_tokens=item.completion_tokens,
            duration_ms=item.duration_ms,
        )
        self._bump("written")

    def shutdown(self, drain: bool = True, timeout: float = 5.0) -> None:
        """Stop the writer. With ``drain``, queued traces are written first."""
        with self._worker_lock:
            self._stopped = True
            worker = self._worker
            self._worker = None
        if worker is None:
            return
        if not drain:
            self._discarding.set()
        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full:
            self._discarding.set()
            logger.warning("Chat trace queue still full at shutdown; dropping pending traces")
        worker.join(timeout)
        if worker.is_alive():
            logger.warning("Chat trace writer did not stop within %.1fs", timeout)
