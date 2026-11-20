import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Optional

BUCKET_SECONDS = 60.0
BUCKET_COUNT = 60
SHORT_WINDOW_BUCKETS = 5

REJECTION_QUEUE_FULL = "queue_full"
REJECTION_PREVIEW_BUSY = "preview_busy"


@dataclass
class _Bucket:
    index: int
    busy_seconds: float = 0.0
    capacity_seconds: float = 0.0
    peak_running: int = 0
    peak_queued: int = 0
    wait_total: float = 0.0
    wait_count: int = 0
    wait_max: float = 0.0
    queue_full: int = 0
    preview_busy: int = 0


class PoolMetrics:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: Deque[_Bucket] = deque(maxlen=BUCKET_COUNT)
        self._last = clock()
        self._running = 0
        self._queued = 0
        self._limit = 0

    def _bucket(self, index: int) -> _Bucket:
        if self._buckets and self._buckets[-1].index == index:
            return self._buckets[-1]
        bucket = _Bucket(index=index)
        bucket.peak_running = self._running
        bucket.peak_queued = self._queued
        self._buckets.append(bucket)
        return bucket

    def _advance(self, now: float) -> _Bucket:
        cursor = self._last
        if now > cursor and (self._running or self._limit):
            while cursor < now:
                index = int(cursor // BUCKET_SECONDS)
                edge = (index + 1) * BUCKET_SECONDS
                span = min(now, edge) - cursor
                bucket = self._bucket(index)
                bucket.busy_seconds += self._running * span
                bucket.capacity_seconds += self._limit * span
                cursor += span
        self._last = max(self._last, now)
        return self._bucket(int(now // BUCKET_SECONDS))

    def observe(self, running: int, queued: int, limit: int) -> None:
        with self._lock:
            bucket = self._advance(self._clock())
            self._running, self._queued, self._limit = running, queued, limit
            bucket.peak_running = max(bucket.peak_running, running)
            bucket.peak_queued = max(bucket.peak_queued, queued)

    def record_wait(self, seconds: float) -> None:
        with self._lock:
            bucket = self._advance(self._clock())
            seconds = max(0.0, seconds)
            bucket.wait_total += seconds
            bucket.wait_count += 1
            bucket.wait_max = max(bucket.wait_max, seconds)

    def record_rejection(self, kind: str) -> None:
        with self._lock:
            bucket = self._advance(self._clock())
            if kind == REJECTION_PREVIEW_BUSY:
                bucket.preview_busy += 1
            else:
                bucket.queue_full += 1

    def summary(self) -> Dict[str, object]:
        with self._lock:
            now = self._clock()
            current = self._advance(now).index
            recent = [b for b in self._buckets if current - b.index < BUCKET_COUNT]
            short = [b for b in recent if current - b.index < SHORT_WINDOW_BUCKETS]
            waits = sum(b.wait_count for b in recent)
            return {
                "window_minutes": BUCKET_COUNT,
                "peak_concurrent": max((b.peak_running for b in recent), default=0),
                "peak_queued": max((b.peak_queued for b in recent), default=0),
                "wait_avg_seconds": round(sum(b.wait_total for b in recent) / waits, 3) if waits else 0.0,
                "wait_max_seconds": round(max((b.wait_max for b in recent), default=0.0), 3),
                "started_jobs": waits,
                "rejected_queue_full": sum(b.queue_full for b in recent),
                "rejected_preview_busy": sum(b.preview_busy for b in recent),
                "utilisation_5m_percent": _utilisation(short),
                "utilisation_60m_percent": _utilisation(recent),
            }


def _utilisation(buckets) -> Optional[float]:
    capacity = sum(b.capacity_seconds for b in buckets)
    if capacity <= 0:
        return None
    return round(100.0 * sum(b.busy_seconds for b in buckets) / capacity, 1)
