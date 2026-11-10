from __future__ import annotations

import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator, Optional

from .errors import SamplingCancelled


class CancellationScope:
    priority = 0

    def __init__(self, is_cancelled: Callable[[], bool], step_index: int = 0) -> None:
        self.is_cancelled = is_cancelled
        self.step_index = step_index

    def on_start(self, total_steps: int) -> None:
        pass

    def on_step(self, step_index, total_steps, x, sigma, denoised_x0) -> None:
        self.step_index = step_index + 1

    def on_end(self) -> None:
        pass


_active_scope: ContextVar[Optional[CancellationScope]] = ContextVar("native_cancellation_scope", default=None)


@contextmanager
def cancellation_scope(
    is_cancelled: Optional[Callable[[], bool]], *, step_index: int = 0,
) -> Iterator[Optional[CancellationScope]]:
    if is_cancelled is None:
        yield None
        return
    scope = CancellationScope(is_cancelled, step_index)
    token = _active_scope.set(scope)
    try:
        yield scope
    except Exception as exc:
        traceback.clear_frames(exc.__traceback__)
        raise
    finally:
        _active_scope.reset(token)


def raise_if_cancelled(block_index: int) -> None:
    scope = _active_scope.get()
    if scope is not None and scope.is_cancelled():
        raise SamplingCancelled(step_index=scope.step_index, block_index=block_index)
