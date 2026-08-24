"""
Unified generation status tracking.

Single in-memory store for generation state and progress, replacing the
three parallel copies that used to exist (GenerationOrchestrator.generation_statuses,
NativeBackend.GenerationStatus, ComfyUIBackend.GenerationStatus). Backends are
stateless executors; only the orchestrator (via this tracker) knows about
generation state, and it is the only place that writes generation status to
the database.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Dict, List, Optional

from src.pipelines.outputs import GenerationOutput
from src.features.generation.repository import generation_repo

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


class GenerationState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class GenerationRecord:
    """In-memory record of a single generation's state and progress."""
    id: str
    preset_id: Optional[str] = None
    backend_id: Optional[str] = None
    user_id: Optional[str] = None
    tab_id: Optional[str] = None
    state: GenerationState = GenerationState.PENDING
    progress: Optional[float] = None
    current_step: Optional[str] = None
    current_step_num: Optional[int] = None
    total_steps: Optional[int] = None
    message: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    # Set when the queue dispatches the generation. `created_at` is enqueue
    # time, so only `started_at` measures execution rather than queue wait.
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def model_dump(self) -> Dict[str, Any]:
        """
        Dict shape matching the previous ``GenerationStatus`` DTO
        (``src.features.generation.dto.GenerationStatus.model_dump()``), so
        controllers/serializers that expect ``.model_dump()`` keep working
        unchanged.
        """
        return {
            'id': self.id,
            'status': self.state.value,
            'preset_id': self.preset_id,
            'progress': self.progress,
            'current_step': self.current_step,
            'total_steps': self.total_steps,
            'current_step_num': self.current_step_num,
            'message': self.message,
            'created_at': str(self.created_at),
            'completed_at': str(self.completed_at) if self.completed_at is not None else None,
        }


class GenerationStatusTracker:
    """
    Single owner of generation state.

    ``transition`` is the ONE place that writes generation status to the
    database (``generation_repo.update_status``); ``update_from_output`` is
    the ONE copy of progress-from-output math.
    """

    def __init__(self):
        self._lock = RLock()
        self._records: Dict[str, GenerationRecord] = {}

    def create(
        self,
        id: str,
        preset_id: Optional[str] = None,
        backend_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tab_id: Optional[str] = None,
    ) -> GenerationRecord:
        record = GenerationRecord(
            id=id,
            preset_id=preset_id,
            backend_id=backend_id,
            user_id=user_id,
            tab_id=tab_id,
        )
        with self._lock:
            self._records[id] = record
        return record

    def update_from_output(self, id: str, output: GenerationOutput) -> None:
        with self._lock:
            record = self._records.get(id)
            if record is None:
                return
            if getattr(output, 'progress', None) is not None:
                record.progress = self._calculate_progress(output.progress)
            if getattr(output, 'current_step', None) is not None:
                record.current_step = output.current_step
            if getattr(output, 'total_steps', None) is not None:
                record.total_steps = output.total_steps
            if getattr(output, 'current_step_num', None) is not None:
                record.current_step_num = output.current_step_num

    @staticmethod
    def _calculate_progress(progress: Any) -> float:
        """Handle both Progress objects (current/max) and direct float values."""
        if hasattr(progress, 'current') and hasattr(progress, 'max'):
            if progress.max > 0:
                return progress.current / progress.max
            return 0.0
        return float(progress)

    def transition(
        self,
        id: str,
        state: GenerationState,
        error: Optional[str] = None,
    ) -> Optional[GenerationRecord]:
        """Transition a generation to a new state and persist it to the database."""
        with self._lock:
            record = self._records.get(id)
            if record is None:
                return None
            if record.state.value in TERMINAL_STATES and state != record.state:
                logger.debug(
                    f"[STATUS_TRACKER] Refusing {record.state.value} -> {state.value} for {id}: terminal state is final"
                )
                return record
            record.state = state
            if error is not None:
                record.error = error
                record.message = error
            if state == GenerationState.RUNNING and record.started_at is None:
                record.started_at = time.time()
            if state in (GenerationState.COMPLETED, GenerationState.FAILED, GenerationState.CANCELLED):
                record.completed_at = time.time()

        try:
            generation_repo.update_status(id, state.value, error_message=error)
        except Exception as e:
            logger.error(f"[STATUS_TRACKER] Failed to persist status for {id}: {e}")

        return record

    async def transition_async(
        self,
        id: str,
        state: GenerationState,
        error: Optional[str] = None,
    ) -> Optional[GenerationRecord]:
        """`transition()` off the event loop, for async call sites.

        The in-memory mutation and the DB write both stay inside the single
        `to_thread` call, so `_lock` still serializes concurrent transitions
        against each other regardless of which thread each one lands on -
        moving the call to a thread doesn't change what's mutually exclusive.
        Awaiting this from each call site preserves the sequencing that a
        cancelled generation can never be overwritten by a later failed
        write: the next transition doesn't start until this one, DB write
        included, has returned.
        """
        return await asyncio.to_thread(self.transition, id, state, error)

    def get(self, id: str) -> Optional[GenerationRecord]:
        with self._lock:
            return self._records.get(id)

    def list_active(self) -> List[GenerationRecord]:
        with self._lock:
            return [
                r for r in self._records.values()
                if r.state in (GenerationState.PENDING, GenerationState.RUNNING)
            ]

    def list_all(self) -> List[GenerationRecord]:
        with self._lock:
            return list(self._records.values())

    def prune_finished(self, max_age_s: float = 3600) -> int:
        """Remove terminal-state records older than ``max_age_s``. Fixes the never-pruned leak."""
        now = time.time()
        with self._lock:
            stale_ids = [
                id for id, r in self._records.items()
                if r.state.value in TERMINAL_STATES
                and r.completed_at is not None
                and (now - r.completed_at) > max_age_s
            ]
            for id in stale_ids:
                del self._records[id]
        return len(stale_ids)
