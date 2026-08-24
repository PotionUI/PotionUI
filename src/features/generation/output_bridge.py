"""
OutputBridge: a single non-blocking, non-dropping thread -> event-loop
funnel for generation outputs.

Replaces the two blocking bridges that used to exist
(``GenerationWorker.on_yield`` in generation.py with
``future.result(timeout=1.0)`` + bare ``except: pass``, and ComfyUIBackend's
``thread_safe_callback`` with ``future.result(timeout=5.0)``), both of which
could block the CUDA worker thread on a WS/DB round-trip and silently drop
outputs on timeout.

``emit()`` is safe to call from any thread (including the pipe-execution
thread) and never blocks the caller. A single asyncio consumer task
(``run()``) dispatches queued outputs to ``on_output`` in order.
"""

import asyncio
import logging
from collections import deque
from typing import Awaitable, Callable, Optional

from src.pipelines.outputs import GenerationOutput, ProgressGenerationOutput

logger = logging.getLogger(__name__)


class OutputBridge:
    """
    Per-generation funnel: sync ``emit()`` callable from any thread, ordered
    async consumer via ``run()``.

    Backpressure policy: once the queue holds ``maxsize`` items, further
    ``ProgressGenerationOutput`` items are coalesced (the newest progress
    output replaces the most recently queued one) instead of growing the
    queue. Media/artifact outputs and the completion sentinel (``None``)
    are never dropped or coalesced - they always enqueue, growing the queue
    past ``maxsize`` if necessary, to guarantee delivery.
    """

    def __init__(
        self,
        on_output: Callable[[Optional[GenerationOutput]], Awaitable[None]],
        maxsize: int = 256,
    ):
        self._on_output = on_output
        self._maxsize = maxsize
        self._loop = asyncio.get_running_loop()
        self._deque: deque = deque()
        self._not_empty = asyncio.Event()
        self._closed = False

    def emit(self, output: Optional[GenerationOutput]) -> None:
        """
        Thread-safe, non-blocking. Safe to call from the CUDA/pipe-execution
        thread, the event loop thread, or anywhere else.
        """
        if self._closed:
            return
        try:
            self._loop.call_soon_threadsafe(self._put, output)
        except RuntimeError:
            # Event loop already closed (e.g. shutdown race) - nothing more
            # we can do; drop rather than raise into the caller's thread.
            logger.warning("[OUTPUT_BRIDGE] emit() called after event loop closed; output dropped")

    def _put(self, output: Optional[GenerationOutput]) -> None:
        """Runs on the event loop thread via call_soon_threadsafe."""
        if output is None:
            self._closed = True
            self._deque.append(None)
            self._not_empty.set()
            return

        if isinstance(output, ProgressGenerationOutput) and len(self._deque) >= self._maxsize:
            # Coalesce: replace the most recently queued progress item
            # instead of growing the queue unbounded.
            for i in range(len(self._deque) - 1, -1, -1):
                if isinstance(self._deque[i], ProgressGenerationOutput):
                    self._deque[i] = output
                    self._not_empty.set()
                    return
            # No coalescable item queued (all media/artifacts) - fall
            # through and enqueue so the update isn't lost entirely.

        self._deque.append(output)
        self._not_empty.set()

    async def run(self) -> None:
        """
        Single consumer task. Dispatches queued outputs to ``on_output`` in
        order (FIFO), preserving ordering by construction since completion
        (``None``) flows through the same queue. Exits after the sentinel.
        """
        while True:
            while not self._deque:
                self._not_empty.clear()
                await self._not_empty.wait()
            item = self._deque.popleft()
            try:
                await self._on_output(item)
            except Exception:
                logger.error("[OUTPUT_BRIDGE] Error in output consumer", exc_info=True)
            if item is None:
                break
