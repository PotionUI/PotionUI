from __future__ import annotations

import logging
import multiprocessing
import os
import sys
import threading
import types
from concurrent.futures import ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_SPAWN_TIMEOUT_SECONDS = 120.0
_POLL_SECONDS = 0.1
_JOIN_SECONDS = 5.0

_SPAWN_FAILURES = (OSError, RuntimeError, ImportError, NotImplementedError, TimeoutError, BrokenProcessPool)

_call_lock = threading.Lock()
_state_lock = threading.Lock()
_pool: Optional[ProcessPoolExecutor] = None


class OffloadCancelled(Exception):
    pass


class OffloadError(RuntimeError):
    pass


def ping() -> dict:
    return {"pid": os.getpid(), "torch": "torch" in sys.modules}


def _spawn_pool() -> ProcessPoolExecutor:
    pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    real_main = sys.modules.get("__main__")
    sys.modules["__main__"] = types.ModuleType("__main__")
    try:
        pool.submit(ping).result(timeout=_SPAWN_TIMEOUT_SECONDS)
    except BaseException:
        _terminate(pool)
        raise
    finally:
        if real_main is not None:
            sys.modules["__main__"] = real_main
    return pool


def _terminate(pool: ProcessPoolExecutor) -> None:
    processes = list((getattr(pool, "_processes", None) or {}).values())
    for process in processes:
        try:
            process.kill()
        except Exception:
            logger.exception("offload worker could not be killed")
    pool.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        try:
            process.join(timeout=_JOIN_SECONDS)
        except Exception:
            logger.exception("offload worker did not exit")


def _get_pool() -> Optional[ProcessPoolExecutor]:
    global _pool
    with _state_lock:
        if _pool is None:
            try:
                _pool = _spawn_pool()
            except _SPAWN_FAILURES as exc:
                logger.error("offload worker process cannot be spawned: %s", exc, exc_info=True)
                return None
        return _pool


def _discard(pool: ProcessPoolExecutor) -> None:
    global _pool
    with _state_lock:
        if _pool is pool:
            _pool = None
    _terminate(pool)


def _acquire(is_cancelled: Optional[Callable[[], bool]]) -> None:
    while not _call_lock.acquire(timeout=_POLL_SECONDS):
        if is_cancelled is not None and is_cancelled():
            raise OffloadCancelled()


def run_offloaded(
    func: Callable[..., Any],
    *args: Any,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> Any:
    _acquire(is_cancelled)
    try:
        if is_cancelled is not None and is_cancelled():
            raise OffloadCancelled()
        pool = _get_pool()
        if pool is None:
            logger.warning(
                "running %s inline: the offload worker is unavailable, so this call blocks the "
                "interpreter for its whole duration",
                getattr(func, "__qualname__", func),
            )
            return func(*args)
        try:
            future = pool.submit(func, *args)
        except BrokenProcessPool as exc:
            _discard(pool)
            raise OffloadError(f"offload worker is broken: {exc}") from exc
        while True:
            done, _ = wait([future], timeout=_POLL_SECONDS)
            if done:
                break
            if is_cancelled is not None and is_cancelled():
                _discard(pool)
                raise OffloadCancelled()
        try:
            return future.result()
        except BrokenProcessPool as exc:
            _discard(pool)
            raise OffloadError(f"offload worker died while running {getattr(func, '__qualname__', func)}: {exc}") from exc
    finally:
        _call_lock.release()


def shutdown_offload() -> None:
    global _pool
    with _state_lock:
        pool, _pool = _pool, None
    if pool is not None:
        _terminate(pool)
