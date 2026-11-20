import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence

from src.features.media_tools.dto import (
    TERMINAL_STATUSES,
    MediaToolItem,
    PreviewClip,
    RunDTO,
    RunError,
    RunSummaryDTO,
)
from src.features.media_tools.pool_limits import PoolLimits
from src.features.media_tools.pool_metrics import REJECTION_QUEUE_FULL, PoolMetrics
from src.platform.http.base_controller import PassThrough
from src.platform.http.errors import DomainError
from src.platform.media_tools import MediaToolError, ToolCancelled

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 2
DEFAULT_RETENTION_SECONDS = 600.0
DEFAULT_MAX_QUEUED_PER_USER = 8
CANCEL_WAIT_SECONDS = 5.0
SHUTDOWN_WAIT_SECONDS = 4.0
GENERIC_FAILURE = "The tool could not finish"


@dataclass
class RunOutcome:
    items: List[MediaToolItem] = field(default_factory=list)
    replaced: bool = False
    preview: Optional[PreviewClip] = None
    summary: Optional[RunSummaryDTO] = None


@dataclass
class _Run:
    run_id: str
    user_id: str
    work: Callable[["RunControl"], RunOutcome]
    kind: str = "run"
    status: str = "queued"
    progress: float = 0.0
    label: str = ""
    items: List[MediaToolItem] = field(default_factory=list)
    replaced: bool = False
    preview: Optional[PreviewClip] = None
    error: Optional[RunError] = None
    summary: Optional[RunSummaryDTO] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    finished_at: Optional[float] = None
    callbacks: List[Callable[[], None]] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    seq: int = 0
    queued_at: float = 0.0
    started_at: Optional[float] = None
    queued_wall: float = 0.0
    started_wall: Optional[float] = None


class RunControl:
    def __init__(self, manager: "MediaToolJobManager", run: _Run):
        self._manager = manager
        self._run = run

    def report(self, fraction: float, label: str = "") -> None:
        self._manager._report(self._run, fraction, label)

    def is_cancelled(self) -> bool:
        return self._run.cancel_event.is_set()


class MediaToolJobManager:
    def __init__(
        self,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        cancel_wait_seconds: float = CANCEL_WAIT_SECONDS,
        max_queued_per_user: int = DEFAULT_MAX_QUEUED_PER_USER,
        limits: Optional[Callable[[], PoolLimits]] = None,
        metrics: Optional[PoolMetrics] = None,
        wall_clock: Callable[[], float] = time.time,
    ):
        static = PoolLimits(
            max_concurrent=max(1, int(max_concurrent)),
            max_queued_per_user=max(1, int(max_queued_per_user)),
        )
        self._limits_source: Callable[[], PoolLimits] = limits if limits is not None else (lambda: static)
        self._metrics = metrics if metrics is not None else PoolMetrics(clock)
        self._wall_clock = wall_clock
        self._sequence = 0
        self._served: Dict[str, int] = {}
        self._served_ticket = 0
        self._closed = False
        self._retention = retention_seconds
        self._clock = clock
        self._cancel_wait = cancel_wait_seconds
        self._lock = threading.RLock()
        self._runs: Dict[str, _Run] = {}
        self._queue: Deque[str] = deque()
        self._running = 0

    def limits(self) -> PoolLimits:
        return self._limits_source()

    @property
    def metrics(self) -> PoolMetrics:
        return self._metrics

    def submit(
        self,
        user_id: str,
        work: Callable[[RunControl], RunOutcome],
        kind: str = "run",
        tools: Sequence[str] = (),
    ) -> str:
        run = _Run(run_id=uuid.uuid4().hex, user_id=user_id, work=work, kind=kind, tools=list(tools))
        limits = self.limits()
        with self._lock:
            if self._closed:
                raise DomainError("shutting_down", "The server is shutting down", status_code=503)
            self._purge()
            queued = sum(1 for other in self._runs.values() if other.user_id == user_id and other.status == "queued")
            if queued >= limits.max_queued_per_user:
                self._metrics.record_rejection(REJECTION_QUEUE_FULL)
                raise DomainError(
                    "too_many_queued",
                    f"You already have {queued} runs waiting; let some finish first",
                    status_code=429,
                )
            self._sequence += 1
            run.seq = self._sequence
            run.queued_at = self._clock()
            run.queued_wall = self._wall_clock()
            self._runs[run.run_id] = run
            self._queue.append(run.run_id)
            self._observe(limits)
        self._pump()
        return run.run_id

    def get(self, user_id: str, run_id: str) -> Optional[RunDTO]:
        with self._lock:
            self._purge()
            run = self._owned(user_id, run_id)
            return self._snapshot(run) if run else None

    def list(self, user_id: str) -> List[RunDTO]:
        with self._lock:
            self._purge()
            return [self._snapshot(run) for run in self._runs.values() if run.user_id == user_id]

    def cancel(self, user_id: str, run_id: str) -> Optional[RunDTO]:
        with self._lock:
            self._purge()
            run = self._owned(user_id, run_id)
        return self._cancel(run) if run is not None else None

    def cancel_any(self, run_id: str) -> Optional[RunDTO]:
        with self._lock:
            self._purge()
            run = self._runs.get(run_id)
        return self._cancel(run) if run is not None else None

    def _cancel(self, run: _Run) -> RunDTO:
        with self._lock:
            if run.status in TERMINAL_STATUSES:
                return self._snapshot(run)
            run.cancel_event.set()
            if run.status == "queued":
                callbacks = self._settle_queued(run)
                self._observe()
            else:
                callbacks = None
            if callbacks is not None:
                snapshot = self._snapshot(run)
        if callbacks is not None:
            self._fire(callbacks)
            return snapshot
        run.done.wait(self._cancel_wait)
        with self._lock:
            return self._snapshot(run)

    def on_done(self, user_id: str, run_id: str, callback: Callable[[], None]) -> bool:
        with self._lock:
            run = self._owned(user_id, run_id)
            if run is None:
                return False
            if run.status not in TERMINAL_STATUSES:
                run.callbacks.append(callback)
                return True
        callback()
        return True

    def wait(self, user_id: str, run_id: str, timeout: Optional[float]) -> Optional[RunDTO]:
        with self._lock:
            run = self._owned(user_id, run_id)
        if run is None:
            return None
        run.done.wait(timeout)
        with self._lock:
            return self._snapshot(run)

    def counts(self) -> Dict[str, int]:
        with self._lock:
            tally = {"queued": 0, "running": 0, "finished": 0}
            for run in self._runs.values():
                if run.status == "queued":
                    tally["queued"] += 1
                elif run.status == "running":
                    tally["running"] += 1
                else:
                    tally["finished"] += 1
            return tally

    def shutdown(self, timeout: float = SHUTDOWN_WAIT_SECONDS) -> None:
        with self._lock:
            self._closed = True
            runs = list(self._runs.values())
            pending: List[List[Callable[[], None]]] = []
            for run in runs:
                if run.status == "queued":
                    run.cancel_event.set()
                    pending.append(self._settle_queued(run))
                elif run.status not in TERMINAL_STATUSES:
                    run.cancel_event.set()
        for callbacks in pending:
            self._fire(callbacks)
        deadline = time.monotonic() + max(0.0, timeout)
        for run in runs:
            if run.status == "running":
                run.done.wait(max(0.0, deadline - time.monotonic()))

    def join_all(self, timeout: float = CANCEL_WAIT_SECONDS) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            if run.status not in TERMINAL_STATUSES:
                run.cancel_event.set()
        for run in runs:
            if run.status == "running":
                run.done.wait(timeout)

    def _settle_queued(self, run: _Run) -> List[Callable[[], None]]:
        run.status = "cancelled"
        run.finished_at = self._clock()
        run.done.set()
        callbacks, run.callbacks = run.callbacks, []
        return callbacks

    @staticmethod
    def _fire(callbacks: List[Callable[[], None]]) -> None:
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logger.exception("A media tool run callback failed")

    def _owned(self, user_id: str, run_id: str) -> Optional[_Run]:
        run = self._runs.get(run_id)
        return run if run is not None and run.user_id == user_id else None

    def _observe(self, limits: Optional[PoolLimits] = None) -> None:
        queued = sum(1 for run in self._runs.values() if run.status == "queued")
        self._metrics.observe(self._running, queued, (limits or self.limits()).max_concurrent)

    def _running_by_user(self) -> Dict[str, int]:
        tally: Dict[str, int] = {}
        for run in self._runs.values():
            if run.status == "running":
                tally[run.user_id] = tally.get(run.user_id, 0) + 1
        return tally

    def pool_state(self) -> Dict[str, Any]:
        self._pump()
        limits = self.limits()
        with self._lock:
            self._purge()
            now = self._clock()
            jobs = []
            for run in sorted(self._runs.values(), key=lambda item: item.seq):
                if run.status not in ("queued", "running"):
                    continue
                since = run.started_at if run.status == "running" and run.started_at is not None else run.queued_at
                jobs.append({
                    "run_id": run.run_id,
                    "user_id": run.user_id,
                    "kind": run.kind,
                    "status": run.status,
                    "tools": list(run.tools),
                    "label": run.label,
                    "progress": round(run.progress, 3) if run.status == "running" else None,
                    "queued_at": run.queued_wall,
                    "started_at": run.started_wall,
                    "elapsed_seconds": round(max(0.0, now - since), 3),
                    "waited_seconds": round(
                        max(0.0, (run.started_at if run.started_at is not None else now) - run.queued_at), 3
                    ),
                })
            counts = self.counts()
            return {
                "limits": limits,
                "running": counts["running"],
                "queued": counts["queued"],
                "jobs": jobs,
                "metrics": self._metrics.summary(),
            }

    def _purge(self) -> None:
        now = self._clock()
        expired = [
            run_id
            for run_id, run in self._runs.items()
            if run.status in TERMINAL_STATUSES
            and run.finished_at is not None
            and now - run.finished_at >= self._retention
        ]
        for run_id in expired:
            del self._runs[run_id]
        if expired:
            active = {run.user_id for run in self._runs.values()}
            for user_id in [user for user in self._served if user not in active]:
                del self._served[user_id]

    def _snapshot(self, run: _Run) -> RunDTO:
        completed = run.status == "completed"
        return RunDTO(
            run_id=run.run_id,
            kind=run.kind,
            status=run.status,
            progress=run.progress,
            label=run.label,
            items=list(run.items) if completed else [],
            replaced=run.replaced if completed else False,
            preview=run.preview if completed else None,
            error=run.error,
            summary=run.summary if completed else None,
        )

    def _report(self, run: _Run, fraction: float, label: str) -> None:
        with self._lock:
            if run.status != "running":
                return
            run.progress = max(run.progress, min(1.0, max(0.0, float(fraction))))
            if label:
                run.label = label

    def _pick(self, limits: PoolLimits) -> Optional[_Run]:
        running = self._running_by_user()
        running_batch: Dict[str, int] = {}
        for run in self._runs.values():
            if run.status == "running" and run.kind != "preview":
                running_batch[run.user_id] = running_batch.get(run.user_id, 0) + 1
        heads: Dict[Any, _Run] = {}
        for run_id in self._queue:
            run = self._runs.get(run_id)
            if run is None or run.status != "queued":
                continue
            heads.setdefault((run.user_id, run.kind == "preview"), run)
        best: Optional[_Run] = None
        best_key = None
        for (user_id, interactive), run in heads.items():
            if (
                not interactive
                and limits.max_running_per_user > 0
                and running_batch.get(user_id, 0) >= limits.max_running_per_user
            ):
                continue
            key = (0 if interactive else 1, running.get(user_id, 0), self._served.get(user_id, 0), run.seq)
            if best_key is None or key < best_key:
                best, best_key = run, key
        return best

    def _pump(self) -> None:
        starters: List[_Run] = []
        limits = self.limits()
        with self._lock:
            self._queue = deque(
                run_id for run_id in self._queue
                if run_id in self._runs and self._runs[run_id].status == "queued"
            )
            while self._running < limits.max_concurrent and self._queue:
                run = self._pick(limits)
                if run is None:
                    break
                self._queue.remove(run.run_id)
                self._served_ticket += 1
                self._served[run.user_id] = self._served_ticket
                run.status = "running"
                run.started_at = self._clock()
                run.started_wall = self._wall_clock()
                self._running += 1
                self._metrics.record_wait(run.started_at - run.queued_at)
                starters.append(run)
            if starters:
                self._observe(limits)
        for run in starters:
            threading.Thread(
                target=self._execute, args=(run,), name=f"media-tool-{run.run_id[:8]}", daemon=True
            ).start()

    def _execute(self, run: _Run) -> None:
        control = RunControl(self, run)
        outcome: Optional[RunOutcome] = None
        error: Optional[RunError] = None
        status = "completed"
        try:
            if run.cancel_event.is_set():
                raise ToolCancelled()
            outcome = run.work(control)
        except ToolCancelled:
            status = "cancelled"
        except MediaToolError as exc:
            status, error = "failed", RunError(code=exc.code, message=exc.message)
        except DomainError as exc:
            status, error = "failed", RunError(code=exc.code, message=exc.message)
        except PassThrough as exc:
            status = "failed"
            error = RunError(code=str(getattr(exc, "error", "refused")), message=str(exc))
        except Exception:
            logger.exception("Media tool run %s failed", run.run_id)
            status, error = "failed", RunError(code="tool_failed", message=GENERIC_FAILURE)
        self._finish(run, status, outcome, error)

    def _finish(
        self, run: _Run, status: str, outcome: Optional[RunOutcome], error: Optional[RunError]
    ) -> None:
        with self._lock:
            run.status = status
            run.error = error
            if status == "completed" and outcome is not None:
                run.items = list(outcome.items)
                run.replaced = outcome.replaced
                run.preview = outcome.preview
                run.summary = outcome.summary
                run.progress = 1.0
            run.finished_at = self._clock()
            self._running -= 1
            self._observe()
            run.done.set()
            callbacks, run.callbacks = run.callbacks, []
        self._fire(callbacks)
        self._pump()
