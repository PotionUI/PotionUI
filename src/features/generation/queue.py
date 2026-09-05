"""
The generation queue: one execution slot per backend, dispatch order set by
that backend's scheduling policy.

Before this existed, `orchestrator.start_generation` handed work straight to
`backend.start_generation`, which spawned a thread immediately. Two users - or
one user with two tabs - could therefore drive the same backend concurrently.

The queue is the thing that enforces the invariant the rest of the code already
assumed: a backend executes exactly one generation at a time. Backends still run
in parallel with each other, since each owns its own `GenerationEngine`.

By default every backend is FIFO: dispatch is arrival order, only skipping
items whose backend is busy, so a native job waiting on the GPU does not block
a ComfyUI job queued behind it. A backend can instead be configured "fair"
(see `docs/backends.md` "Scheduling policy"), which round-robins between
users with a model-affinity allowance; the actual per-backend choice of which
pending item runs next lives in `scheduling.py`, kept pure and separately
tested so the fairness rules can be pinned without a running queue.

The queue holds only *pending* work. Once dispatched, a generation's state lives
in `GenerationStatusTracker`; the queue keeps just the backend slot until the
orchestrator calls `release`.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.features.generation.scheduling import (
    BackendSchedulingState,
    SchedulingPolicy,
    project_order,
    select_next,
)
from src.platform.observability.logger import logger


@dataclass
class QueuedGeneration:
    """A generation waiting for its backend to free up."""

    generation_id: str
    backend_id: str
    user_id: Optional[str] = None
    tab_id: Optional[str] = None
    enqueued_at: float = field(default_factory=time.time)
    # The model this generation targets, for the "fair" policy's model
    # affinity - see GenerationOrchestrator._resolve_model_key. Opaque to the
    # queue beyond equality: it is never dereferenced, only compared to the
    # backend's currently-loaded key.
    model_key: Optional[str] = None
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
    Queue with per-backend concurrency of 1; per-backend FIFO or fair scheduling.

    All public methods are coroutines and must be awaited on the event loop;
    the queue is not thread-safe by design, because every caller (the API
    controllers and the orchestrator) already runs there.

    Dispatch order is FIFO by default. `policy_for(backend_id)` can resolve a
    backend to a "fair" `SchedulingPolicy` instead, which round-robins between
    users with a model-affinity allowance - see `scheduling.select_next`. The
    two policies only ever affect *which pending item* is picked next; the
    one-slot-per-backend and dispatch-failure invariants are unchanged.
    """

    def __init__(
        self,
        dispatch: Callable[[QueuedGeneration], Awaitable[None]],
        policy_for: Optional[Callable[[str], SchedulingPolicy]] = None,
    ):
        self._dispatch = dispatch
        # None means every backend is plain FIFO - the pre-fair-scheduling default.
        self._policy_for = policy_for or (lambda backend_id: SchedulingPolicy())
        self._pending: List[QueuedGeneration] = []
        # backend_id -> generation_id currently occupying that backend's slot
        self._busy: Dict[str, str] = {}
        # backend_id -> that backend's fairness bookkeeping (rotation cursor,
        # loaded model, consecutive-dispatch count). Absent == fresh/FIFO.
        self._scheduling_state: Dict[str, BackendSchedulingState] = {}
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

    def _select_ready_item_locked(self) -> Optional[QueuedGeneration]:
        """
        Pick the next item to dispatch, if any backend is both idle and has
        pending work. Must be called with `self._lock` held.

        Considers one idle backend per call (the first one, by where its
        earliest pending item sits in `self._pending`); `_pump`'s loop calls
        this repeatedly, so every idle backend with work still gets dispatched
        in the same `_pump()` invocation. Committing the fairness state here
        - before `_dispatch` is even awaited - is deliberate: a turn is
        considered taken the moment it's selected, so a later dispatch
        failure frees the backend slot without also handing that turn back
        (see `test_dispatch_failure_does_not_burn_the_other_users_turn`).
        """
        backend_id = next(
            (i.backend_id for i in self._pending if i.backend_id not in self._busy),
            None,
        )
        if backend_id is None:
            return None

        policy = self._policy_for(backend_id)
        state = self._scheduling_state.get(backend_id, BackendSchedulingState())
        item, new_state = select_next(self._pending, backend_id, policy, state)
        self._scheduling_state[backend_id] = new_state
        return item

    async def _pump(self, raise_for: Optional[str] = None) -> None:
        """
        Dispatch every item whose backend is idle, per that backend's policy.

        `raise_for` names the one generation whose dispatch failure should be
        re-raised to the caller. It is re-raised only after the rest of the
        queue has been pumped, so one bad item never strands the others.
        """
        deferred_error: Optional[BaseException] = None

        while True:
            async with self._lock:
                item = self._select_ready_item_locked()
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

    def _projected_pending(self) -> List[QueuedGeneration]:
        """Pending items ordered by projected dispatch sequence, per backend.

        Groups `self._pending` by backend (in each backend's first-arrival
        order, matching pre-fair-scheduling behaviour when only one backend
        is involved), then simulates each backend's policy forward from its
        real, persisted scheduling state via `scheduling.project_order` - a
        pure simulation that never mutates that state. A FIFO backend's items
        come back untouched; a fair backend's come back in the order they
        will actually dispatch, so `position()`/`pending_items()`/`snapshot()`
        agree with what the user will actually see happen.
        """
        by_backend: Dict[str, List[QueuedGeneration]] = {}
        backend_order: List[str] = []
        for item in self._pending:
            if item.backend_id not in by_backend:
                by_backend[item.backend_id] = []
                backend_order.append(item.backend_id)
            by_backend[item.backend_id].append(item)

        projected: List[QueuedGeneration] = []
        for backend_id in backend_order:
            policy = self._policy_for(backend_id)
            state = self._scheduling_state.get(backend_id, BackendSchedulingState())
            projected.extend(project_order(by_backend[backend_id], policy, state))
        return projected

    def position(self, generation_id: str) -> Optional[int]:
        """Zero-based position among pending items (projected dispatch order),
        or None if not queued."""
        for index, item in enumerate(self._projected_pending()):
            if item.generation_id == generation_id:
                return index
        return None

    def running_generation_id(self, backend_id: str) -> Optional[str]:
        return self._busy.get(backend_id)

    def pending_items(self) -> List[QueuedGeneration]:
        """Pending work in projected dispatch order; index is the queue position."""
        return self._projected_pending()

    def pending_for_tab(self, user_id: str, tab_id: str) -> List[QueuedGeneration]:
        return [
            i for i in self._projected_pending()
            if i.tab_id == tab_id and i.user_id == user_id
        ]

    def snapshot(self) -> Dict[str, Any]:
        """Serializable view of the queue, for the API and `queue_update` pushes."""
        return {
            "pending": [i.to_dict() for i in self._projected_pending()],
            "running": dict(self._busy),
        }
