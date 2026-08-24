"""Backend-owned chat turns: a turn outlives the request that started it.

A chat turn — LLM streaming, the tool loop, and the assistant-message
persistence that happens at its end — used to run *inside* the SSE request
generator. A client disconnect (page reload mid-response) made Starlette cancel
that generator, killing the turn and losing the already-paid-for response.

Here a turn is an ``asyncio`` task owned by a per-process ``ChatTurnRegistry``.
The task drives the existing ``ConversationRunner`` event stream to completion
regardless of who is listening, fanning each event into (a) a replay buffer of
everything the turn has emitted so far and (b) any live subscriber queues. An
SSE connection is now just a subscriber: it can drop and reconnect (or a second
tab can attach) and still see the whole turn from the start.

Single-process assumption: this registry is in-memory. PotionUI runs as one
uvicorn process, so a turn and its subscribers always share the loop; there is
no cross-process turn discovery and none is built.
"""

import asyncio
import logging
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

StreamFactory = Callable[[], AsyncGenerator[dict, None]]


class ChatTurn:
    """One in-flight or finished chat turn and its event fan-out.

    ``events`` is the ordered replay buffer of every event the turn has emitted.
    Producer (``_emit``) and subscriber registration (``add_subscriber``) never
    ``await`` between snapshotting the buffer and mutating the subscriber set,
    so on the single event loop they are atomic with respect to each other — a
    late subscriber cannot miss an event nor receive one twice.
    """

    def __init__(self, session_id: str, user_id: str):
        self.turn_id = uuid.uuid4().hex
        self.session_id = session_id
        self.user_id = user_id
        self.status = "running"  # running | completed | error | cancelled
        self.events: list = []
        self._subscribers: set = set()
        self.done = asyncio.Event()
        self.task: Optional[asyncio.Task] = None
        self._cancel_requested = False

    @property
    def is_done(self) -> bool:
        return self.status != "running"

    def _emit(self, event: dict) -> None:
        """Append an event to the buffer and hand it to every live subscriber."""
        self.events.append(event)
        for q in self._subscribers:
            q.put_nowait(event)

    def _finish(self, status: str) -> None:
        """Mark the turn finished and close out every subscriber's stream."""
        self.status = status
        for q in self._subscribers:
            q.put_nowait(_SENTINEL)
        self._subscribers.clear()
        self.done.set()

    def add_subscriber(self) -> "asyncio.Queue":
        """Register a subscriber, pre-loaded with the full replay buffer.

        The returned queue already holds every event emitted so far (in order);
        a still-running turn also gets future events, a finished turn gets the
        terminating sentinel instead.
        """
        q: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            q.put_nowait(event)
        if self.is_done:
            q.put_nowait(_SENTINEL)
        else:
            self._subscribers.add(q)
        return q

    def _remove_subscriber(self, q: "asyncio.Queue") -> None:
        self._subscribers.discard(q)

    async def stream(self) -> AsyncGenerator[dict, None]:
        """Yield this turn's events (replayed from the start, then live) until it ends."""
        q = self.add_subscriber()
        try:
            while True:
                item = await q.get()
                if item is _SENTINEL:
                    return
                yield item
        finally:
            self._remove_subscriber(q)

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

    def __init__(self, turn_timeout_seconds: int = 1800):
        # Safety net only: a hard ceiling so a wedged LLM call can't leave a turn
        # task running forever. Distinct from (and far larger than) the per-call
        # LLM request timeout — this bounds the whole turn including the tool loop.
        self._turn_timeout_seconds = turn_timeout_seconds
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

        turn = ChatTurn(session_id=session_id, user_id=user_id)
        self._turns[session_id] = turn
        turn.task = asyncio.create_task(self._drive(turn, stream_factory))
        self._evict_if_needed()
        return turn

    def _evict_if_needed(self) -> None:
        """Bound retained turns by dropping the oldest finished one over the cap."""
        while len(self._turns) > _MAX_RETAINED_TURNS:
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
