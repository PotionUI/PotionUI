"""Sync-to-async bridge shared by the notification and automation connection
managers: schedule a coroutine from non-async code, using the running loop's
`create_task` when called from async code on the loop thread, otherwise
falling back to `run_coroutine_threadsafe` against the loop captured via
`set_loop()`/first `connect()` (e.g. a worker thread completing a generation).
"""
import asyncio
import logging
from typing import Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


class LoopDispatchMixin:
    """Mixin providing `set_loop`/`schedule_send`. Subclasses call
    `_init_loop_dispatch()` in `__init__` and implement `schedule_send` in
    terms of `_schedule(make_coro)`, where `make_coro` builds the coroutine
    to run (a per-user send, a broadcast, ...)."""

    _DISPATCH_LABEL = "send"

    def _init_loop_dispatch(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # No captured loop yet -> every schedule call before the first
        # connect() hits the same "dropping message" branch; warn once
        # instead of on every dropped message.
        self._warned_no_loop = False

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running event loop (called from api.py's lifespan)."""
        self._loop = loop

    def _schedule(self, make_coro: Callable[[], Coroutine]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(make_coro())
            return
        except RuntimeError:
            pass

        if self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(make_coro(), self._loop)
            except Exception as e:
                logger.error(f"Failed to schedule {self._DISPATCH_LABEL}: {e}")
        elif not self._warned_no_loop:
            self._warned_no_loop = True
            logger.warning("schedule_send called with no running loop and no captured loop; dropping message")
