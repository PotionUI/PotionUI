"""Per-bucket wall-time accounting for :func:`ar_loop.generate`, feeding the
same ``GenerationProfiler`` stage table as ``native.move_to`` / ``load.dit.read``
(see ``src.platform.observability.profiling.profiler.GenerationProfiler.mark``).

CUDA is asynchronous, so a plain ``perf_counter()`` wrapped around a launch
only measures CPU dispatch. :class:`ArTiming` measures both: CPU wall-time via
``perf_counter`` and GPU time via a ``torch.cuda.Event`` pair per call,
resolved with a single ``torch.cuda.synchronize()`` at :meth:`ArTiming.emit`
-- never per frame, so the hot loop never blocks on the device.

Entirely inert when profiling is off: :func:`ArTiming.__init__` reads
``profiling_enabled()`` once (already cached, see ``profiler.py``) and
:meth:`ArTiming.track` hands back a shared no-op context manager without
touching ``perf_counter`` or allocating an ``Event``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import torch

from src.platform.observability.profiling.profiler import get_profiler, profiling_enabled

_BUCKETS = ("prefill", "lm_step", "depth", "sampling_feedback")


@dataclass
class _Bucket:
    cpu_s: float = 0.0
    calls: int = 0
    events: list[tuple["torch.cuda.Event", "torch.cuda.Event"]] = field(default_factory=list)


class _NullTrack:
    """Returned by :meth:`ArTiming.track` when profiling is off -- a bare
    context manager, no timer state, no branch inside ``__exit__``."""

    __slots__ = ()

    def __enter__(self) -> "_NullTrack":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


_NULL_TRACK = _NullTrack()


class _Track:
    __slots__ = ("_bucket", "_use_cuda_events", "_t0", "_start_evt")

    def __init__(self, bucket: _Bucket, use_cuda_events: bool) -> None:
        self._bucket = bucket
        self._use_cuda_events = use_cuda_events

    def __enter__(self) -> "_Track":
        if self._use_cuda_events:
            self._start_evt = torch.cuda.Event(enable_timing=True)
            self._start_evt.record()
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> bool:
        if self._use_cuda_events:
            end_evt = torch.cuda.Event(enable_timing=True)
            end_evt.record()
            self._bucket.events.append((self._start_evt, end_evt))
        self._bucket.cpu_s += time.perf_counter() - self._t0
        self._bucket.calls += 1
        return False


class ArTiming:
    """One instance per :func:`ar_loop.generate` call. ``bool(self.active)``
    is false whenever profiling is off, so callers that want to skip the
    ``with timing.track(...):`` wrapping entirely (not required -- it's
    already a no-op) can branch on it too."""

    def __init__(self, device: torch.device) -> None:
        self.active = profiling_enabled()
        self._device = device
        self._use_cuda_events = self.active and device.type == "cuda"
        self._buckets = {name: _Bucket() for name in _BUCKETS} if self.active else None

    def track(self, bucket: str):
        if not self.active:
            return _NULL_TRACK
        return _Track(self._buckets[bucket], self._use_cuda_events)

    def emit(self, frame_count: int) -> None:
        if not self.active:
            return
        if self._use_cuda_events:
            torch.cuda.synchronize(self._device)
        profiler = get_profiler()
        for name, bucket in self._buckets.items():
            gpu_s = sum(s.elapsed_time(e) for s, e in bucket.events) / 1000.0 if bucket.events else 0.0
            profiler.mark(f"ar.{name}", cpu_s=round(bucket.cpu_s, 4), gpu_s=round(gpu_s, 4), calls=bucket.calls)
        profiler.mark("ar.frames", frames=frame_count)
