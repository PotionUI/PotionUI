"""
The generation queue: one global FIFO, one execution slot per backend.

Before this existed, `orchestrator.start_generation` handed work straight to
`backend.start_generation`, which spawned a thread immediately. Two users - or
one user with two tabs - could therefore drive the same backend concurrently.

The queue is the thing that enforces the invariant the rest of the code already
assumed: a backend executes exactly one generation at a time. Backends still run
in parallel with each other, since each owns its own `GenerationEngine`.

Ordering is global FIFO, but dispatch skips items whose backend is busy. A
native job waiting on the GPU therefore does not block a ComfyUI job queued
behind it, while two native jobs still run strictly in order.

The queue holds only *pending* work. Once dispatched, a generation's state lives
in `GenerationStatusTracker`; the queue keeps just the backend slot until the
orchestrator calls `release`.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.platform.observability.logger import logger


@dataclass
class QueuedGeneration:
    """A generation waiting for its backend to free up."""

    generation_id: str
    backend_id: str
    user_id: Optional[str] = None
    tab_id: Optional[str] = None
    enqueued_at: float = field(default_factory=time.time)
    # Opaque to the queue; handed back to the dispatcher verbatim.
    payload: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "backend_id": self.backend_id,
            "tab_id": self.tab_id,
            "enqueued_at": self.enqueued_at,
        }


class GenerationQueue:
    """
    FIFO queue with per-backend concurrency of 1.

    All public methods are coroutines and must be awaited on the event loop;
    the queue is not thread-safe by design, because every caller (the API
    controllers and the orchestrator) already runs there.
    """

    def __init__(self, dispatch: Callable[[QueuedGeneration], Awaitable[None]]):
        self._dispatch = dispatch
        self._pending: List[QueuedGeneration] = []
        # backend_id -> generation_id currently occupying that backend's slot
        self._busy: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, item: QueuedGeneration) -> None:
        """
        Append `item` and dispatch whatever is now runnable.

        When `item`'s backend is idle it dispatches inline, and a failure to
        start it (e.g. an invalid pipeline) propagates to the caller so the API
        can answer with an error rather than a generation id that is already
        dead. A failure to start some *other* item is only logged - the caller
        did not ask for it and must not inherit its exception.
        """
        async with self._lock:
            self._pending.append(item)
        logger.info(
            f"[QUEUE] Enqueued {item.generation_id} for backend {item.backend_id} "
            f"(tab={item.tab_id})"
        )
        await self._pump(raise_for=item.generation_id)

    async def release(self, backend_id: str, generation_id: str) -> None:
        """
        Free `backend_id`'s slot, then dispatch the next runnable item.

        The `generation_id` guard matters: a late completion from an already
        superseded generation must not free the slot of the one that replaced it.
        """
        async with self._lock:
            if self._busy.get(backend_id) != generation_id:
                return
            del self._busy[backend_id]
        logger.debug(f"[QUEUE] Released backend {backend_id} from {generation_id}")
        await self._pump()

    async def _pump(self, raise_for: Optional[str] = None) -> None:
        """
        Dispatch every item whose backend is idle, in FIFO order.

        `raise_for` names the one generation whose dispatch failure should be
        re-raised to the caller. It is re-raised only after the rest of the
        queue has been pumped, so one bad item never strands the others.
        """
        deferred_error: Optional[BaseException] = None

        while True:
            async with self._lock:
                item = next(
                    (i for i in self._pending if i.backend_id not in self._busy),
                    None,
                )
                if item is None:
                    break
                self._pending.remove(item)
                self._busy[item.backend_id] = item.generation_id

            try:
                await self._dispatch(item)
            except Exception as e:
                # The dispatcher is responsible for marking the generation
                # FAILED; the queue's only job is to not strand the slot.
                async with self._lock:
                    if self._busy.get(item.backend_id) == item.generation_id:
                        del self._busy[item.backend_id]

                if item.generation_id == raise_for:
                    deferred_error = e
                else:
                    logger.error(
                        f"[QUEUE] Dispatch of {item.generation_id} failed: {e}", exc_info=True
                    )

        if deferred_error is not None:
            raise deferred_error

    async def cancel(self, generation_id: str) -> bool:
        """
        Drop a *pending* generation. Returns True if it was queued.

        A queued generation has never touched a backend, so cancelling it is a
        list removal - no GPU work, no cancellation flag. Returns False when the
        generation is running (the caller must cancel it on the backend instead).
        """
        async with self._lock:
            for item in self._pending:
                if item.generation_id == generation_id:
                    self._pending.remove(item)
                    logger.info(f"[QUEUE] Removed pending generation {generation_id}")
                    return True
        return False

    async def clear_tab(self, user_id: str, tab_id: str) -> List[str]:
        """
        Drop every pending generation belonging to `(user_id, tab_id)`.

        Scoped by user as well as tab because tab ids are minted client-side and
        are only unique within a user. Running generations are left alone.
        """
        async with self._lock:
            doomed = [
                i for i in self._pending
                if i.tab_id == tab_id and i.user_id == user_id
            ]
            for item in doomed:
                self._pending.remove(item)
        ids = [i.generation_id for i in doomed]
        if ids:
            logger.info(f"[QUEUE] Cleared {len(ids)} pending generation(s) from tab {tab_id}")
        return ids

    def position(self, generation_id: str) -> Optional[int]:
        """Zero-based position among pending items, or None if not queued."""
        for index, item in enumerate(self._pending):
            if item.generation_id == generation_id:
                return index
        return None

    def running_generation_id(self, backend_id: str) -> Optional[str]:
        return self._busy.get(backend_id)

    def pending_items(self) -> List[QueuedGeneration]:
        """Pending work in FIFO order; index is the queue position."""
        return list(self._pending)

    def pending_for_tab(self, user_id: str, tab_id: str) -> List[QueuedGeneration]:
        return [
            i for i in self._pending
            if i.tab_id == tab_id and i.user_id == user_id
        ]

    def snapshot(self) -> Dict[str, Any]:
        """Serializable view of the queue, for the API and `queue_update` pushes."""
        return {
            "pending": [i.to_dict() for i in self._pending],
            "running": dict(self._busy),
        }
