"""Bounded worker boundary for the synchronous generation-history unit of work.

Listing history is one long synchronous call: repository queries, bulk
hydration of files and tags, serialization, and - for a semantic query - text
embedding plus a vector search. Run on the event loop it stalls every other
request in the process for the whole duration, so the whole call is handed to a
worker thread as a single unit (never one hop per row or per query).

This module owns its own `ThreadPoolExecutor` rather than using
`asyncio.to_thread`. The default loop executor is unbounded: a burst of
gallery requests would spawn a thread each and let SQLite thrash. Here the
worker count is capped at `MAX_WORKERS` and admission is capped at
`MAX_WORKERS + MAX_PENDING` in-flight calls, past which `run()` raises
`HistoryExecutorSaturated` immediately instead of queueing without bound - the
caller turns that into a 503 and the client retries, which is a better failure
than a request queue that grows until the process dies.

Each admitted call holds its slot until the *worker* finishes, not until the
awaiting coroutine returns: a client that disconnects mid-request cancels its
await and drops the result, but the thread it started is uninterruptible and
still owns a database connection, so its slot is only released when it is
genuinely done.

Safe on worker threads because `Database.get_connection()` opens a fresh
`sqlite3.connect(..., check_same_thread=False)` per call and closes it in the
same `with` block - no connection is shared across the boundary.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

MAX_WORKERS = 4
MAX_PENDING = 32


class HistoryExecutorSaturated(Exception):
    """Admission was refused: the boundary is at capacity, or shutting down.

    Deliberately not a `GenerationException` - nothing about the requested
    generation is wrong, the server is simply out of history workers, and the
    controller maps it to a 503 rather than to any of the domain statuses.
    """
    pass


class HistoryExecutor:
    """Runs synchronous history work off the event loop, under a hard cap."""

    def __init__(
        self,
        max_workers: int = MAX_WORKERS,
        max_pending: int = MAX_PENDING,
    ):
        self._max_workers = max_workers
        self._capacity = max_workers + max_pending
        self._lock = threading.Lock()
        self._inflight = 0
        self._closed = False
        self._executor: Optional[ThreadPoolExecutor] = None

    async def run(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Await `fn(*args, **kwargs)` on a worker thread.

        Raises:
            HistoryExecutorSaturated: capacity is exhausted, or the boundary
                has been shut down. Nothing was submitted.
        """
        loop = asyncio.get_running_loop()

        with self._lock:
            if self._closed:
                raise HistoryExecutorSaturated("History executor is shutting down")
            if self._inflight >= self._capacity:
                raise HistoryExecutorSaturated(
                    f"History executor is at capacity ({self._capacity} in flight)"
                )
            self._inflight += 1
            executor = self._ensure_executor()

        try:
            work = executor.submit(fn, *args, **kwargs)
        except BaseException:
            self._release()
            raise

        work.add_done_callback(self._on_work_done)
        return await asyncio.wrap_future(work, loop=loop)

    def shutdown(self) -> None:
        """Drop the pool without waiting on running work.

        Queued-but-unstarted calls are cancelled (their waiters see a
        `CancelledError`); threads already running finish and release their
        slots on the way out.
        """
        with self._lock:
            self._closed = True
            executor, self._executor = self._executor, None

        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    def _ensure_executor(self) -> ThreadPoolExecutor:
        """Caller holds `_lock`."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="generation-history",
            )
        return self._executor

    def _on_work_done(self, _future: Any) -> None:
        self._release()

    def _release(self) -> None:
        with self._lock:
            self._inflight -= 1
