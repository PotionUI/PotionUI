"""Queue-facing half of generation orchestration.

The orchestrator decides *what* to run; this decides *when*. It owns the single
`GenerationQueue`, turns a freed backend slot into a running generation, and
keeps every waiting generation's WebSocket position up to date. Dispatch itself
is delegated back to the orchestrator through the injected callback, since only
the orchestrator knows how to build and start a pipeline.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from src.features.generation.preview_clip_message import ADMIN_DETAIL_KEY
from src.features.generation.queue import GenerationQueue, QueuedGeneration
from src.features.generation.scheduling import SchedulingPolicy
from src.features.generation.status_tracker import (
    GenerationState,
    GenerationStatusTracker,
)

logger = logging.getLogger(__name__)


WAITING_FOR_BACKEND = "Waiting for a server to come back online."


class CancelledBeforeStart(Exception):
    pass


class QueueDispatcher:
    """Owns the generation queue and drives dispatch of queued work.

    A generation only becomes RUNNING here, when the queue hands its backend
    slot over. The orchestrator supplies the `dispatch` callback that actually
    builds and starts the pipeline.
    """

    def __init__(
        self,
        status_tracker: GenerationStatusTracker,
        dispatch: Callable[..., Any],
        policy_for: Optional[Callable[[str], SchedulingPolicy]] = None,
        capacity_for: Optional[Callable[[str], int]] = None,
        hold_reason: Optional[Callable[[str], Optional[str]]] = None,
    ):
        """Initialize the dispatcher.

        Args:
            status_tracker: Single owner of generation state/progress.
            dispatch: Coroutine that starts a generation on its backend, called
                with (generation_id, request, backend, db_generation,
                output_callback). Supplied by the orchestrator.
            policy_for: Resolves a backend id to its configured
                `SchedulingPolicy` (FIFO or fair). `None` (most tests) makes
                every backend FIFO, matching pre-fair-scheduling behaviour.
                Supplied by the composition root, which reads it off the
                backend's persisted settings.
            capacity_for: Resolves a backend id to how many generations it
                runs at once. `None` gives every backend one slot.
            hold_reason: Answers why a backend cannot start queued work right
                now, or `None` when it can. Held work stays pending.
        """
        self.status_tracker = status_tracker
        self._dispatch = dispatch
        self._hold_reason = hold_reason or (lambda backend_id: None)

        self.queue = GenerationQueue(
            dispatch=self._dispatch_queued,
            policy_for=policy_for,
            capacity_for=capacity_for,
            is_dispatchable=lambda backend_id: self._hold_reason(backend_id) is None,
        )
        # Set by the controller; pushes `queue_update` to a generation's WS
        # subscribers when its position changes or it starts running.
        self._queue_listener: Optional[Callable[[str, Dict[str, Any]], Any]] = None

    def set_queue_listener(self, listener: Callable[[str, Dict[str, Any]], Any]) -> None:
        """Register the callback that broadcasts `queue_update` messages."""
        self._queue_listener = listener

    async def enqueue(self, item: QueuedGeneration) -> None:
        """Enqueue work; the queue dispatches it synchronously if the backend is idle."""
        await self.queue.enqueue(item)

    def position(self, generation_id: str) -> Optional[int]:
        return self.queue.position(generation_id)

    async def cancel(self, generation_id: str) -> bool:
        """Drop a still-queued generation; True if it was found and removed."""
        return await self.queue.cancel(generation_id)

    async def release(self, backend_id: str, generation_id: str) -> None:
        """Free a backend slot so the queue can dispatch whatever waits on it."""
        await self.queue.release(backend_id, generation_id)

    def _admin_detail(self, item: QueuedGeneration, hold: Optional[str]) -> Dict[str, Any]:
        payload = item.payload if isinstance(item.payload, dict) else {}
        backend = payload.get('backend')
        detail = {
            'backend_id': item.backend_id,
            'backend_name': getattr(backend, 'name', None) or item.backend_id,
        }
        if hold:
            detail['waiting_detail'] = hold
        return detail

    def _pending_message(self, item: QueuedGeneration, index: int, depth: int) -> Dict[str, Any]:
        hold = self._hold_reason(item.backend_id)
        return {
            'type': 'queue_update',
            'generation_id': item.generation_id,
            'tab_id': item.tab_id,
            'status': 'pending',
            'queue_position': index,
            'queue_depth': depth,
            'waiting_reason': WAITING_FOR_BACKEND if hold else None,
            ADMIN_DETAIL_KEY: self._admin_detail(item, hold),
        }

    def queue_info(self, generation_id: str) -> Optional[Dict[str, Any]]:
        for item, index, depth in self.queue.pending_with_positions():
            if item.generation_id == generation_id:
                message = self._pending_message(item, index, depth)
                return {key: message[key] for key in (
                    'queue_position', 'queue_depth', 'waiting_reason', ADMIN_DETAIL_KEY,
                )}
        return None

    async def wake(self) -> None:
        await self.queue.wake()

    def is_idle(self) -> bool:
        return self.queue.is_idle()

    async def publish_positions(self) -> None:
        """
        Tell every still-pending generation where it now sits.

        Positions shift whenever anything is enqueued, dispatched or cancelled,
        so this runs after each of those. Best-effort: a broadcast failure must
        never break the queue.
        """
        if self._queue_listener is None:
            return
        for item, index, depth in self.queue.pending_with_positions():
            try:
                await self._queue_listener(item.generation_id, self._pending_message(item, index, depth))
            except Exception as e:
                logger.error(f"Failed to publish queue position for {item.generation_id}: {e}")

    async def _dispatch_queued(self, item: QueuedGeneration) -> None:
        """
        Run a generation whose backend slot has just been claimed by the queue.

        This is the only place a generation becomes RUNNING. Emitting the
        transition here (rather than at enqueue) is what makes `started_at`
        meaningful, so `duration_ms` measures execution and not queue wait.
        """
        payload = item.payload
        record = await self.status_tracker.transition_async(item.generation_id, GenerationState.RUNNING)
        if record is not None and record.state == GenerationState.CANCELLED:
            await self.queue.release(item.backend_id, item.generation_id)
            return

        if self._queue_listener is not None:
            try:
                await self._queue_listener(item.generation_id, {
                    'type': 'queue_update',
                    'generation_id': item.generation_id,
                    'tab_id': item.tab_id,
                    'status': 'running',
                    'queue_position': None,
                    ADMIN_DETAIL_KEY: self._admin_detail(item, None),
                })
            except Exception as e:
                logger.error(f"Failed to publish queue start for {item.generation_id}: {e}")

        try:
            await self._dispatch(
                item.generation_id,
                payload['request'],
                payload['backend'],
                payload['db_generation'],
                payload['output_callback'],
            )
        except CancelledBeforeStart:
            await self.queue.release(item.backend_id, item.generation_id)

    def prune_finished(self) -> None:
        """Drop terminal-state records older than the default age from the
        in-memory status tracker (best-effort - a pruning failure must never
        break generation handling). Called wherever a generation reaches a
        terminal state, since without it `GenerationStatusTracker._records`
        grows for the lifetime of the process."""
        try:
            self.status_tracker.prune_finished()
        except Exception as e:
            logger.debug(f"[ORCHESTRATOR] prune_finished failed: {e}")

    async def clear_tab_queue(self, user_id: str, tab_id: str) -> List[str]:
        """
        Drop every *pending* generation belonging to a tab.

        The tab's running generation, if any, is left alone - cancel it
        explicitly. Returns the ids that were dropped.
        """
        cancelled = await self.queue.clear_tab(user_id, tab_id)
        for generation_id in cancelled:
            await self.status_tracker.transition_async(generation_id, GenerationState.CANCELLED)
        self.prune_finished()
        await self.publish_positions()
        logger.info(f"Cleared {len(cancelled)} queued generation(s) from tab {tab_id}")
        return cancelled

    def get_queue_snapshot(
        self,
        user_id: str,
        tab_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        pending = []
        for item, index, depth in self.queue.pending_with_positions():
            if item.user_id != user_id:
                continue
            if tab_id is not None and item.tab_id != tab_id:
                continue
            entry = item.to_dict()
            entry['queue_position'] = index
            entry['queue_depth'] = depth
            entry['waiting_reason'] = WAITING_FOR_BACKEND if self._hold_reason(item.backend_id) else None
            pending.append(entry)

        running = [
            {
                'generation_id': record.id,
                'backend_id': record.backend_id,
                'preset_id': record.preset_id,
                'tab_id': record.tab_id,
                'progress': record.progress,
            }
            for record in self.status_tracker.list_active()
            if record.state == GenerationState.RUNNING and record.user_id == user_id
            and (tab_id is None or record.tab_id == tab_id)
        ]

        return {'pending': pending, 'running': running}

    def get_all_queue_snapshot(self) -> Dict[str, Any]:
        pending = []
        for item, index, depth in self.queue.pending_with_positions():
            record = self.status_tracker.get(item.generation_id)
            pending.append({
                'generation_id': item.generation_id,
                'backend_id': item.backend_id,
                'backend_name': self._admin_detail(item, None)['backend_name'],
                'queue_depth': depth,
                'waiting_detail': self._hold_reason(item.backend_id),
                'preset_id': record.preset_id if record else None,
                'tab_id': item.tab_id,
                'user_id': item.user_id,
                'queue_position': index,
                'created_at': item.enqueued_at,
            })

        running = [
            {
                'generation_id': record.id,
                'backend_id': record.backend_id,
                'preset_id': record.preset_id,
                'tab_id': record.tab_id,
                'user_id': record.user_id,
                'progress': record.progress,
                'current_step': record.current_step,
                'created_at': record.created_at,
                'started_at': record.started_at,
            }
            for record in self.status_tracker.list_active()
            if record.state == GenerationState.RUNNING
        ]

        return {'pending': pending, 'running': running}
