"""A background thread running one long-lived asyncio event loop, dedicated
to the download worker's queue-consumer tasks (see `manager.py`).

Why this exists: the queue consumer (`DownloadWorker._worker`) must stay
alive for as long as the process runs, but the manager can be started - or a
download queued - from very different moments:

1. App startup, before uvicorn's own event loop exists - a task scheduled on
   whatever throwaway loop is around then is simply destroyed ("Task was
   destroyed but it is pending!") the moment uvicorn's loop takes over.
2. A live request handler on the real uvicorn loop.
3. A setup-run executor or a sync lazy-loader (`ensure_local_hf_repo`)
   bridging from a worker thread via `run_sync()`/`run_coroutine_threadsafe`,
   whose throwaway per-call loops die the moment the call returns.

Only (2) is safe without this; (1) and (3) would orphan the consumer's tasks
the instant their throwaway loop goes away, leaving queued downloads stuck at
`status='pending'` forever. Rather than guessing which caller is "the real
one", the consumer gets its own loop that none of them own and none of them
can tear down - started once, lazily, and transparently replaced if it ever
dies.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class PersistentLoop:
    """One background daemon thread running one asyncio event loop forever.

    `ensure_running()` is the only entry point that matters: it returns a
    loop that is guaranteed to be alive right now, starting the thread on
    first use and transparently restarting it if a previous thread died
    (self-healing - see module docstring). Callers schedule work onto the
    returned loop with `asyncio.run_coroutine_threadsafe`.
    """

    def __init__(self, name: str = "persistent-loop"):
        self._name = name
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    def is_alive(self) -> bool:
        """True if the background thread is currently running its loop."""
        return self._thread is not None and self._thread.is_alive()

    def ensure_running(self) -> asyncio.AbstractEventLoop:
        """Return a loop that is running right now, (re)starting the
        background thread if it isn't alive."""
        with self._lock:
            if self.is_alive() and self._loop is not None:
                return self._loop

            ready = threading.Event()
            state: dict = {}

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                state["loop"] = loop
                ready.set()
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            thread = threading.Thread(target=_run, name=self._name, daemon=True)
            thread.start()
            if not ready.wait(timeout=10):
                raise RuntimeError(f"Persistent loop '{self._name}' failed to start")

            self._thread = thread
            self._loop = state["loop"]
            logger.debug("Persistent loop '%s' started", self._name)
            return self._loop

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop the loop and join its thread. Best-effort - used by tests
        (and would be used by a plugin disable/app-shutdown hook) to avoid
        leaking daemon threads; nothing calls this in the request path."""
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = None
            self._thread = None

        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=timeout)
