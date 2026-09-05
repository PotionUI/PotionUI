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

# Ceiling on the serialized form of one field — the UTF-8 bytes the column
# actually receives, markers and JSON separators included. A history carrying
# an inline base64 image is megabytes on its own, and a full queue of those
# would be gigabytes resident.
MAX_TRACE_FIELD_BYTES = 64 * 1024

# Ceiling on any one string nested inside a field, applied before the field is
# serialized: it keeps a giant string from ever being rendered to JSON, and it
# leaves an oversized history readable (structure and short messages survive)
# instead of discarding the field wholesale.
MAX_TRACE_STRING_BYTES = 8 * 1024

# Reserved out of a field's budget for the marker entry that replaces what was
# dropped, plus the brackets around it.
_MARKER_ALLOWANCE_BYTES = 256

# The writer waits on the queue in slices rather than on a stop token: a token
# cannot be delivered through a queue that is already full, which is exactly
# the state a shutdown under load has to survive.
WORKER_POLL_SECONDS = 0.1


def _truncation_marker(original_chars: int) -> str:
    return f"\n…[truncated: {original_chars} chars]"


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _exceeds(text: str, limit: int) -> bool:
    """Whether ``text`` encodes to more than ``limit`` UTF-8 bytes.

    Answers from the code-point count where that is already conclusive (a
    UTF-8 encoding is never shorter than, nor more than four times, the number
    of code points), so the encode only ever runs on a bounded slice.
    """
    if len(text) > limit:
        return True
    if len(text) <= limit // 4:
        return False
    return _utf8_len(text) > limit


def _cap_string(value: str, limit: int) -> Tuple[str, bool]:
    """Cut ``value`` to at most ``limit`` UTF-8 bytes, marker included."""
    if not _exceeds(value, limit):
        return value, False
    marker = _truncation_marker(len(value))
    budget = max(limit - _utf8_len(marker), 0)
    head = value[:budget].encode("utf-8")[:budget].decode("utf-8", "ignore")
    return head + marker, True


def _shrink_strings(value: Any) -> Tuple[Any, bool]:
    if isinstance(value, str):
        return _cap_string(value, MAX_TRACE_STRING_BYTES)
    if isinstance(value, dict):
        shrunk: Dict[Any, Any] = {}
        cut = False
        for key, item in value.items():
            shrunk[key], item_cut = _shrink_strings(item)
            cut = cut or item_cut
        return shrunk, cut
    if isinstance(value, list):
        items = []
        cut = False
        for item in value:
            shrunk_item, item_cut = _shrink_strings(item)
            items.append(shrunk_item)
            cut = cut or item_cut
        return items, cut
    return value, False


def _keep_tail(items: List[Any]) -> List[Any]:
    """Keep as many trailing entries as the field budget affords.

    The tail of a message array is the part a debug trace is read for — the
    turn that produced this call — so the head is what gets sacrificed. Each
    entry is charged its own serialized bytes plus the separator that joins it
    to the next one.
    """
    budget = MAX_TRACE_FIELD_BYTES - _MARKER_ALLOWANCE_BYTES
    kept: List[Any] = []
    for item in reversed(items):
        cost = _utf8_len(json.dumps(item, default=str)) + len(", ")
        if cost > budget:
            break
        budget -= cost
        kept.append(item)
    kept.reverse()
    dropped = len(items) - len(kept)
    marker = {
        "role": "system",
        "content": f"[{dropped} earlier entries dropped: field over {MAX_TRACE_FIELD_BYTES} bytes]",
    }
    return [marker] + kept


def _dropped_value(value: Any) -> Any:
    marker = f"[dropped: field over {MAX_TRACE_FIELD_BYTES} bytes]"
    if isinstance(value, list):
        return [{"role": "system", "content": marker}]
    if isinstance(value, dict):
        return {"truncated": marker}
    return marker


def _cap_json(value: Any) -> Tuple[Optional[str], bool]:
    """Serialize a structured field, bounded, returning (json_text, truncated).

    The JSON text *is* the snapshot: it is immutable, so a caller mutating the
    history afterwards cannot change what gets persisted, and its length is the
    memory a queued record holds. Strings are cut before the first ``dumps`` so
    an inline base64 image is never rendered to JSON at all. The writer thread
    decodes the text again for the repository, which owns the encoding of what
    it stores — the same encoder settings, so the bytes measured here are the
    bytes the column receives.
    """
    if value is None:
        return None, False

    shrunk, cut = _shrink_strings(value)
    text = json.dumps(shrunk, default=str)
    if not _exceeds(text, MAX_TRACE_FIELD_BYTES):
        return text, cut

    if isinstance(shrunk, list):
        text = json.dumps(_keep_tail(shrunk), default=str)
        if not _exceeds(text, MAX_TRACE_FIELD_BYTES):
            return text, True

    return json.dumps(_dropped_value(shrunk), default=str), True


def _cap_text(value: Optional[str]) -> Tuple[Optional[str], bool]:
    if value is None:
        return None, False
    return _cap_string(value, MAX_TRACE_FIELD_BYTES)


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
        self._queue: "queue.Queue[_QueuedTrace]" = queue.Queue(maxsize=queue_maxsize)
        # One lock covers admission, the worker handle, the stop flags and the
        # counters, so a record can never be admitted after a shutdown has
        # begun to tear the writer down.
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._stopping = False
        self._draining = True
        self._counters = {"written": 0, "dropped": 0, "discarded": 0, "truncated": 0, "failed": 0}

    def stats(self) -> Dict[str, int]:
        with self._lock:
            counters = dict(self._counters)
        counters["queued"] = self._queue.qsize()
        return counters

    def _bump(self, counter: str) -> None:
        with self._lock:
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
        if not self._enabled():
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
        truncated = any(
            (messages_cut, params_cut, tools_cut, tool_calls_cut, system_cut, response_cut)
        )

        with self._lock:
            if self._stopping:
                self._counters["dropped"] += 1
                return
            self._start_worker_locked()
            try:
                self._queue.put_nowait(item)
            except queue.Full:
                # Drop the newest, never the oldest and never block: the queue
                # is full because the DB is slower than the chat is producing,
                # and the traces already in it are the earlier calls of the
                # same turns — the ones that explain the later ones. Blocking
                # here would put the write back on the response path, which is
                # the whole point.
                self._counters["dropped"] += 1
                return
            if truncated:
                self._counters["truncated"] += 1

    def _start_worker_locked(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(
            target=self._run, name="chat-trace-writer", daemon=True,
        )
        self._worker.start()

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=WORKER_POLL_SECONDS)
            except queue.Empty:
                with self._lock:
                    if self._stopping:
                        return
                continue

            with self._lock:
                if self._stopping and not self._draining:
                    self._counters["discarded"] += 1
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
        """Stop the writer. With ``drain``, queued traces are written first.

        A writer still inside a slow write outlives ``timeout``; it stops on
        its own once that write returns. The handle is kept in that case so a
        later call joins the same thread rather than losing it.
        """
        with self._lock:
            self._stopping = True
            self._draining = drain
            worker = self._worker
        if worker is None:
            return
        worker.join(timeout)
        if worker.is_alive():
            logger.warning(
                "Chat trace writer still busy after %.1fs; it will stop once the write returns",
                timeout,
            )
            return
        with self._lock:
            if self._worker is worker:
                self._worker = None
