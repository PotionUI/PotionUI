"""Backend-owned chat turns: a turn outlives the request that started it.

A chat turn — LLM streaming, the tool loop, and the assistant-message
persistence that happens at its end — used to run *inside* the SSE request
generator. A client disconnect (page reload mid-response) made Starlette cancel
that generator, killing the turn and losing the already-paid-for response.

Here a turn is an ``asyncio`` task owned by a per-process ``ChatTurnRegistry``.
The task drives the existing ``ConversationRunner`` event stream to completion
regardless of who is listening, fanning each event into (a) a bounded replay
buffer of the turn's history so far and (b) any live subscriber queues. An SSE
connection is now just a subscriber: it can drop and reconnect (or a second tab
can attach) and still see the whole turn — from the start, or from a cursor it
already holds.

Replay is read-only fan-out: attaching or reattaching a subscriber only ever
re-yields events the turn already produced. It never re-invokes the stream
factory and never re-runs a tool — the factory is called exactly once per turn,
by the registry's drive task, independent of how many subscribers come and go.

Bounding the replay buffer: a long turn (many tool calls, a long streamed
answer) cannot be replayed in full forever without growing the process's
memory without bound. Once a turn's buffer exceeds ``max_events`` retained
entries or ``max_replay_bytes`` of serialized payload, the OLDEST *compactable*
events (currently: ``token`` deltas and ``status`` pings — the high-volume,
re-derivable ones) are dropped and replaced by a single ``replay_snapshot``
marker carrying the concatenated text of the dropped token deltas (itself
capped at ``_MAX_SNAPSHOT_TEXT_CHARS``) and a sequence cursor. Essential events
— tool calls/results, terminal outcomes, message/title events — are never
compacted away *by choice*, but they are still bounded two ways so the cap
holds for every event class, not just the compactable ones: a single essential
event whose own payload exceeds ``max_essential_event_bytes`` is retained as a
small truncated reference (same type/seq, a short preview, ``truncated:
True``) rather than its full body — live subscribers still get the full event
at emit time, only what's *retained for replay* is shrunk; and if essential
events alone still exceed the caps (no compactable event left to drop), the
oldest ones are folded into the same ``replay_snapshot`` marker as a
``dropped_essential_count``/``dropped_essential_seq_range`` pair instead of
being kept forever, with the newest essential events (most likely to matter
to a reconnecting client) always preserved. Every event carries a monotonic
``seq``; a subscriber can pass ``after_seq`` to replay only what it doesn't
already have, and if the requested prefix was compacted away it transparently
receives the snapshot marker (whose own ``seq`` is the cursor) instead of a
silent gap.

Slow subscribers never block the producer: each subscriber has a bounded
queue, and a subscriber that falls behind has its oldest queued event dropped
in favor of the newest one, with an explicit ``overflow`` event so its stream
can tell it fell behind and resync (via ``after_seq``) rather than silently
missing data. The same bound applies to a fresh subscriber's initial replay
preload: if the retained buffer (up to ``max_events``) is larger than that
subscriber's own queue capacity, the excess oldest events are folded into a
*subscriber-local* snapshot marker (same folding rule as above) rather than
raising ``QueueFull`` or silently truncating — a big retained buffer and a
small queue are two independent, unrelated caps.

Single-process assumption: this registry is in-memory. PotionUI runs as one
uvicorn process, so a turn and its subscribers always share the loop; there is
no cross-process turn discovery and none is built.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncGenerator, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Per-turn queue sentinel: pushed when the turn finishes so a subscriber's
# stream loop terminates cleanly after draining the replay/live events.
_SENTINEL = object()

# Cap on retained turns (one entry per session). Finished turns are kept so a
# reload arriving just after completion can still replay the terminal events;
# the oldest finished entry is evicted once the cap is exceeded.
_MAX_RETAINED_TURNS = 256

# Per-turn replay-buffer caps. Exceeding either triggers compaction of the
# oldest compactable (non-essential) events. Deliberately generous — a normal
# turn (a few tool calls plus a streamed answer) sits far under both.
_DEFAULT_MAX_EVENTS_PER_TURN = 500
_DEFAULT_MAX_REPLAY_BYTES_PER_TURN = 256 * 1024  # 256 KiB of serialized events

# How long a finished turn stays reattachable before it's reclaimed outright,
# independent of the count cap above.
_DEFAULT_FINISHED_TURN_TTL_SECONDS = 600  # 10 minutes

# Per-subscriber live-queue depth. A subscriber that can't keep up has its
# oldest queued event dropped rather than growing this without bound or
# blocking the producer.
_DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE = 200

# Compacted-away token text is kept for reconnect snapshots but itself capped
# so an arbitrarily long streamed answer can't make the marker unbounded.
_MAX_SNAPSHOT_TEXT_CHARS = 8192

# An essential event this large is retained (for replay only — live delivery
# is unaffected) as a truncated reference rather than its full body, so one
# oversized tool result/done payload can't blow the per-turn byte cap on its
# own. The full payload is already durably persisted by the ordinary message/
# tool-execution persistence path this turn's stream factory drives.
_DEFAULT_MAX_ESSENTIAL_EVENT_BYTES = 16 * 1024  # 16 KiB
_ESSENTIAL_PREVIEW_CHARS = 512
_ESSENTIAL_PREVIEW_KEYS = (
    "tool_name", "tool_call_id", "id", "message_id", "session_id", "turn_id",
    "success", "error", "step", "state",
)

# Event types compaction is allowed to drop: high-volume, re-derivable from a
# snapshot. Every other event type (tool calls, terminal outcomes, message/
# title events, and anything not recognized here) is essential and is never
# compacted, on the assumption that an unrecognized event type is more likely
# to matter than not.
_COMPACTABLE_EVENT_TYPES = frozenset({"token", "status"})

StreamFactory = Callable[[], AsyncGenerator[dict, None]]


class _Subscriber:
    """A live subscriber's bounded queue plus its overflow flag.

    Overflow is recorded here rather than acted on immediately in the producer
    (which only ever does the non-blocking drop-oldest-then-push) so the
    consumer can emit exactly one ``overflow`` marker the next time it reads,
    then keep draining whatever is left in the queue.
    """

    __slots__ = ("queue", "overflowed")

    def __init__(self, maxsize: int):
        self.queue: "asyncio.Queue" = asyncio.Queue(maxsize=maxsize)
        self.overflowed = False


class ChatTurn:
    """One in-flight or finished chat turn and its event fan-out.

    ``events`` is the ordered, bounded replay buffer of the turn's history so
    far (see module docstring for the compaction policy). Producer (``_emit``)
    and subscriber registration (``add_subscriber``) never ``await`` between
    snapshotting the buffer and mutating the subscriber set, so on the single
    event loop they are atomic with respect to each other — a late subscriber
    cannot miss an event nor receive one twice.
    """

    def __init__(
        self,
        session_id: str,
        user_id: str,
        max_events: int = _DEFAULT_MAX_EVENTS_PER_TURN,
        max_replay_bytes: int = _DEFAULT_MAX_REPLAY_BYTES_PER_TURN,
        subscriber_queue_maxsize: int = _DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE,
        max_essential_event_bytes: int = _DEFAULT_MAX_ESSENTIAL_EVENT_BYTES,
    ):
        self.turn_id = uuid.uuid4().hex
        self.session_id = session_id
        self.user_id = user_id
        self.status = "running"  # running | completed | error | cancelled
        self.events: list = []
        self.finished_at: Optional[float] = None
        self._subscribers: "set[_Subscriber]" = set()
        self.done = asyncio.Event()
        self.task: Optional[asyncio.Task] = None
        self._cancel_requested = False

        self._max_events = max_events
        self._max_replay_bytes = max_replay_bytes
        self._subscriber_queue_maxsize = subscriber_queue_maxsize
        self._max_essential_event_bytes = max_essential_event_bytes
        self._next_seq = 1
        self._total_bytes = 0
        self._compacted_through = 0
        self._compacted_text = ""
        self._dropped_essential_count = 0
        self._dropped_essential_range: Optional[list] = None
        self._compaction_marker: Optional[dict] = None

        # Set by the registry so a lifecycle boundary (finish, subscriber
        # removal) can trigger its reclamation pass without a background task.
        self._on_change: Optional[Callable[[], None]] = None

    @property
    def is_done(self) -> bool:
        return self.status != "running"

    @staticmethod
    def _event_size(event: dict) -> int:
        try:
            return len(json.dumps(event))
        except (TypeError, ValueError):
            return 0

    def _truncate_essential_event(self, event: dict) -> dict:
        """A retained (replay-buffer) stand-in for an oversized essential event.

        Only affects what's stored for replay — ``_emit`` still hands the full,
        untruncated event to every live subscriber first.
        """
        data = event.get("data") or {}
        preview = {k: data[k] for k in _ESSENTIAL_PREVIEW_KEYS if k in data}
        try:
            raw = json.dumps(data)
        except (TypeError, ValueError):
            raw = str(data)
        preview["preview"] = raw[:_ESSENTIAL_PREVIEW_CHARS]
        preview["truncated"] = True
        return {"event": event.get("event"), "seq": event.get("seq"), "data": preview}

    def _prepare_for_retention(self, event: dict) -> dict:
        """The version of `event` stored in the replay buffer.

        Compactable events are stored as-is (compaction may drop them later);
        an oversized essential event is stored as a truncated reference so one
        huge tool result/done payload can't blow the byte cap on its own.
        """
        if event.get("event") in _COMPACTABLE_EVENT_TYPES:
            return event
        if self._event_size(event) > self._max_essential_event_bytes:
            return self._truncate_essential_event(event)
        return event

    def _emit(self, event: dict) -> None:
        """Hand the full event to every live subscriber, then retain a bounded copy."""
        event = dict(event)
        event["seq"] = self._next_seq
        self._next_seq += 1
        for sub in self._subscribers:
            self._deliver(sub, event)
        stored = self._prepare_for_retention(event)
        self.events.append(stored)
        self._total_bytes += self._event_size(stored)
        self._compact_if_needed()

    def _deliver(self, sub: "_Subscriber", event) -> None:
        """Non-blocking push; a full queue drops its oldest entry first.

        The producer must never await a stalled consumer, so this only ever
        uses ``put_nowait``/``get_nowait``. The subscriber's stream loop turns
        the resulting ``overflowed`` flag into one explicit marker event.
        """
        try:
            sub.queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        try:
            sub.queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        sub.overflowed = True
        try:
            sub.queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # pathological race on a 0/1-sized queue; next emit retries

    def _next_compactable_index(self) -> Optional[int]:
        start = 1 if self._compaction_marker is not None else 0
        for i in range(start, len(self.events)):
            if self.events[i].get("event") in _COMPACTABLE_EVENT_TYPES:
                return i
        return None

    def _next_essential_fallback_index(self) -> Optional[int]:
        """The oldest essential event to fold away as a last resort.

        Only used once no compactable event is left and the buffer is still
        over cap. Never returns the newest (last) event — at least one
        essential event (most likely the terminal outcome) always survives.
        """
        start = 1 if self._compaction_marker is not None else 0
        if len(self.events) - start <= 1:
            return None
        return start

    @staticmethod
    def _fold_event(ev: dict, text: str, cursor: int, dropped_essential: int, drop_range: Optional[list]):
        """Fold one removed event into a running (text, cursor, dropped-essential) state.

        Shared by the turn's own compaction (`_fold_removed_into_state`) and a
        subscriber-local preload collapse (`_collapse_to_capacity`) — the rule
        for "how one event contributes to a snapshot marker" must stay the
        same wherever it's applied.
        """
        seq = ev.get("seq", cursor)
        event_type = ev.get("event")
        if event_type == "token":
            content = ev.get("data", {}).get("content", "")
            text = (text + content)[-_MAX_SNAPSHOT_TEXT_CHARS:]
        elif event_type != "status":
            # Not a compactable type at all — this is the essential-fallback
            # path folding away an essential event as a last resort.
            dropped_essential += 1
            drop_range = [seq, seq] if drop_range is None else [drop_range[0], seq]
        return text, seq, dropped_essential, drop_range

    @staticmethod
    def _marker_from_state(text: str, cursor: int, dropped_essential: int, drop_range: Optional[list]) -> dict:
        data = {"text_so_far": text, "cursor": cursor}
        if dropped_essential:
            data["dropped_essential_count"] = dropped_essential
            data["dropped_essential_seq_range"] = list(drop_range)
        return {"event": "replay_snapshot", "seq": cursor, "data": data}

    def _apply_compaction_marker(self) -> None:
        marker = self._marker_from_state(
            self._compacted_text, self._compacted_through,
            self._dropped_essential_count, self._dropped_essential_range,
        )
        if self._compaction_marker is not None:
            self._total_bytes -= self._event_size(self._compaction_marker)
            self.events[0] = marker
        else:
            self.events.insert(0, marker)
        self._compaction_marker = marker
        self._total_bytes += self._event_size(marker)

    def _fold_removed_into_state(self, removed: dict) -> None:
        self._compacted_text, self._compacted_through, self._dropped_essential_count, self._dropped_essential_range = (
            self._fold_event(
                removed, self._compacted_text, self._compacted_through,
                self._dropped_essential_count, self._dropped_essential_range,
            )
        )
        self._apply_compaction_marker()

    def _compact_if_needed(self) -> None:
        """Drop the oldest events (compactable first) until back under both caps.

        Once no compactable event is left, falls back to folding away the
        oldest *essential* events too (keeping the newest) rather than letting
        an essential-only backlog defeat the cap — see the module docstring.
        """
        while len(self.events) > self._max_events or self._total_bytes > self._max_replay_bytes:
            idx = self._next_compactable_index()
            if idx is None:
                idx = self._next_essential_fallback_index()
            if idx is None:
                return
            removed = self.events.pop(idx)
            self._total_bytes -= self._event_size(removed)
            self._fold_removed_into_state(removed)

    def _finish(self, status: str) -> None:
        """Mark the turn finished and close out every subscriber's stream.

        Uses the same non-raising, drop-oldest-on-full push as live events
        (``_deliver``) rather than a raw ``put_nowait`` — a subscriber whose
        bounded queue is already full at the moment the turn ends must still
        receive its sentinel, not crash the drive task with ``QueueFull``.
        """
        self.status = status
        self.finished_at = time.monotonic()
        for sub in self._subscribers:
            self._deliver(sub, _SENTINEL)
        self._subscribers.clear()
        self.done.set()
        if self._on_change is not None:
            self._on_change()

    def _replay_from(self, after_seq: Optional[int]) -> list:
        """Events to hand a new subscriber: everything, or only what's newer.

        Filtering by ``seq`` naturally includes the compaction marker when the
        caller's cursor predates it (the marker's own ``seq`` is the cursor of
        the last event it absorbed) and skips it otherwise — no special-casing
        needed for "the caller's prefix was compacted away".
        """
        if after_seq is None:
            return list(self.events)
        return [e for e in self.events if e.get("seq", 0) > after_seq]

    @staticmethod
    def _collapse_to_capacity(events: list, capacity: int) -> list:
        """Shrink `events` to at most `capacity` entries: snapshot + tail.

        The retained replay buffer (up to ``max_events``) and one subscriber's
        queue depth are independent caps — a buffer larger than a given
        subscriber's queue must still produce a coherent preload, never a
        ``QueueFull`` and never a silent truncation. Folds the oldest entries
        (seeding from any marker already at the front) into a fresh, local
        ``replay_snapshot`` — this never mutates the turn's own compaction
        state, it only shapes what this one subscriber gets handed.
        """
        if capacity <= 0:
            # A queue too small to hold even one event — best effort: the
            # single newest event, so the subscriber gets *something* live.
            return events[-1:]
        if len(events) <= capacity:
            return events

        text, cursor, dropped_essential, drop_range = "", 0, 0, None
        start = 0
        if events and events[0].get("event") == "replay_snapshot":
            seed = events[0].get("data", {})
            text = seed.get("text_so_far", "")
            cursor = seed.get("cursor", 0)
            dropped_essential = seed.get("dropped_essential_count", 0)
            rng = seed.get("dropped_essential_seq_range")
            drop_range = list(rng) if rng else None
            start = 1

        body = events[start:]
        keep_from = max(len(body) - (capacity - 1), 0)  # reserve one slot for the new marker
        to_fold, tail = body[:keep_from], body[keep_from:]
        for ev in to_fold:
            text, cursor, dropped_essential, drop_range = ChatTurn._fold_event(
                ev, text, cursor, dropped_essential, drop_range
            )

        marker = ChatTurn._marker_from_state(text, cursor, dropped_essential, drop_range)
        return [marker] + tail

    def _build_preload(self, after_seq: Optional[int]) -> list:
        """The event batch to hand a new subscriber, guaranteed to fit its queue.

        Reserves one slot for the terminating sentinel when the turn is
        already finished (``add_subscriber`` pushes it right after).
        """
        replay = self._replay_from(after_seq)
        capacity = self._subscriber_queue_maxsize - (1 if self.is_done else 0)
        if len(replay) <= capacity:
            return replay
        return self._collapse_to_capacity(replay, capacity)

    def add_subscriber(self, after_seq: Optional[int] = None) -> "_Subscriber":
        """Register a subscriber, pre-loaded with the requested replay range.

        A still-running turn also gets future events, a finished turn gets the
        terminating sentinel instead. The preload is delivered through the same
        drop-safe path as live events (``_deliver``) — by construction (see
        ``_build_preload``) it always fits, but routing it through ``_deliver``
        anyway means a preload can never raise even if that invariant is ever
        violated.
        """
        sub = _Subscriber(self._subscriber_queue_maxsize)
        for event in self._build_preload(after_seq):
            self._deliver(sub, event)
        if self.is_done:
            self._deliver(sub, _SENTINEL)
        else:
            self._subscribers.add(sub)
        return sub

    def _remove_subscriber(self, sub: "_Subscriber") -> None:
        self._subscribers.discard(sub)
        if self._on_change is not None:
            self._on_change()

    @staticmethod
    def _overflow_marker() -> dict:
        return {
            "event": "overflow",
            "data": {"message": "This connection fell behind and missed events; resync from the latest state."},
        }

    async def stream(self, after_seq: Optional[int] = None) -> AsyncGenerator[dict, None]:
        """Yield this turn's events (replayed from ``after_seq``, then live) until it ends."""
        sub = self.add_subscriber(after_seq=after_seq)
        try:
            while True:
                item = await sub.queue.get()
                # Checked before the sentinel test: an overflow that dropped
                # the events right up to (or including, via _finish's own
                # drop-to-make-room) the sentinel must never be swallowed by a
                # stream that just quietly ends looking complete.
                if sub.overflowed:
                    sub.overflowed = False
                    yield self._overflow_marker()
                if item is _SENTINEL:
                    return
                yield item
        finally:
            self._remove_subscriber(sub)

    def request_cancel(self) -> None:
        """Ask the turn to stop; the drive task turns this into a cancelled finish."""
        self._cancel_requested = True
        if self.task is not None and not self.task.done():
            self.task.cancel()

    def status_snapshot(self) -> dict:
        return {"turn_id": self.turn_id, "status": self.status}


class TurnAlreadyRunningError(Exception):
    """A turn is already active for the session; a second one is refused."""


class ChatTurnRegistry:
    """Owns the live chat turns of this process, one active turn per session."""

    def __init__(
        self,
        turn_timeout_seconds: int = 1800,
        max_events_per_turn: int = _DEFAULT_MAX_EVENTS_PER_TURN,
        max_replay_bytes_per_turn: int = _DEFAULT_MAX_REPLAY_BYTES_PER_TURN,
        subscriber_queue_maxsize: int = _DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE,
        finished_turn_ttl_seconds: float = _DEFAULT_FINISHED_TURN_TTL_SECONDS,
        max_retained_turns: int = _MAX_RETAINED_TURNS,
        max_essential_event_bytes: int = _DEFAULT_MAX_ESSENTIAL_EVENT_BYTES,
    ):
        # Safety net only: a hard ceiling so a wedged LLM call can't leave a turn
        # task running forever. Distinct from (and far larger than) the per-call
        # LLM request timeout — this bounds the whole turn including the tool loop.
        self._turn_timeout_seconds = turn_timeout_seconds
        self._max_events_per_turn = max_events_per_turn
        self._max_replay_bytes_per_turn = max_replay_bytes_per_turn
        self._subscriber_queue_maxsize = subscriber_queue_maxsize
        self._finished_turn_ttl_seconds = finished_turn_ttl_seconds
        self._max_retained_turns = max_retained_turns
        self._max_essential_event_bytes = max_essential_event_bytes
        self._turns: Dict[str, ChatTurn] = {}

    def active(self, session_id: str) -> Optional[ChatTurn]:
        """The session's turn if it is still running, else None."""
        turn = self._turns.get(session_id)
        if turn is not None and not turn.is_done:
            return turn
        return None

    def get(self, session_id: str) -> Optional[ChatTurn]:
        """The session's most recent turn (running or finished), if retained."""
        return self._turns.get(session_id)

    def start(self, session_id: str, user_id: str, stream_factory: StreamFactory) -> ChatTurn:
        """Begin a turn for the session and drive it in the background.

        Raises:
            TurnAlreadyRunningError: if the session already has a running turn.
        """
        if self.active(session_id) is not None:
            raise TurnAlreadyRunningError(session_id)

        turn = ChatTurn(
            session_id=session_id,
            user_id=user_id,
            max_events=self._max_events_per_turn,
            max_replay_bytes=self._max_replay_bytes_per_turn,
            subscriber_queue_maxsize=self._subscriber_queue_maxsize,
            max_essential_event_bytes=self._max_essential_event_bytes,
        )
        turn._on_change = self._evict_if_needed
        self._turns[session_id] = turn
        turn.task = asyncio.create_task(self._drive(turn, stream_factory))
        self._evict_if_needed()
        return turn

    def _evict_if_needed(self) -> None:
        """Reclaim finished turns past their TTL, then bound retained turns by count.

        Called at every lifecycle boundary (turn start, turn finish, subscriber
        removal) rather than from a background task, so a group of turns that
        all finish after the count cap was exceeded gets reclaimed as soon as
        any one of them hits a boundary — not just on the next ``start()``.
        """
        now = time.monotonic()
        expired = [
            sid for sid, turn in self._turns.items()
            if turn.is_done and turn.finished_at is not None
            and (now - turn.finished_at) >= self._finished_turn_ttl_seconds
        ]
        for sid in expired:
            del self._turns[sid]

        while len(self._turns) > self._max_retained_turns:
            for sid, turn in self._turns.items():
                if turn.is_done:
                    del self._turns[sid]
                    break
            else:
                # Every retained turn is still running (pathological) — stop.
                break

    async def _drive(self, turn: ChatTurn, stream_factory: StreamFactory) -> None:
        """Consume the turn's event stream to completion, fanning events out.

        The stream factory yields the same dicts the SSE layer used to yield
        directly; persistence, hooks and title kickoff happen inside it, so they
        now always run regardless of subscriber presence.
        """
        async def _pump() -> None:
            async for event in stream_factory():
                turn._emit(event)

        try:
            await asyncio.wait_for(_pump(), timeout=self._turn_timeout_seconds)
            turn._finish("completed")
        except asyncio.TimeoutError:
            logger.warning(
                "Chat turn %s for session %s timed out after %ss",
                turn.turn_id, turn.session_id, self._turn_timeout_seconds,
            )
            turn._emit({
                "event": "error",
                "data": {"error": "turn_timeout", "message": "The response timed out."},
            })
            turn._finish("error")
        except asyncio.CancelledError:
            # Explicit cancel (stop button) vs. an unexpected task cancellation.
            turn._emit({
                "event": "generation_cancelled",
                "data": {"session_id": turn.session_id, "turn_id": turn.turn_id},
            })
            turn._finish("cancelled")
            if not turn._cancel_requested:
                # Not our doing — honor the cancellation of the driving task.
                raise
        except Exception as e:  # noqa: BLE001 - surface any turn failure as an event
            logger.exception("Chat turn %s failed: %s", turn.turn_id, e)
            turn._emit({
                "event": "error",
                "data": {"error": "turn_error", "message": str(e)},
            })
            turn._finish("error")
