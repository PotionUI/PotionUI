"""Bridges an async coroutine into a synchronous `StepExecutor.execute()` call.

`StepExecutor.execute` is synchronous (see `base.py`) so the whole run-forward
chain (`SetupExecutorRegistry.execute` -> `SetupRunner.execute_current_step`
-> `SetupRunner.drive`) stays a plain method call, including from route
handlers that are themselves already inside a running event loop. A couple of
executors (`artifacts.fetch`, `generation.smoke`) need to await real async
collaborators (the download queue, the generation orchestrator) -
`asyncio.run()` cannot be called while a loop is already running in the same
thread, so this runs the coroutine on a dedicated worker thread with its own
loop whenever one is already running, and falls back to a plain `asyncio.run()`
otherwise (the common case in tests, which call executors directly with no
event loop of their own).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Awaitable, TypeVar

T = TypeVar("T")


def run_sync(coro: Awaitable[T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()  # type: ignore[arg-type]
