from typing import Optional

from src.features.cloud.clock import Clock, MonotonicClock


class TokenBucket:
    def __init__(self, rate_per_s: Optional[float] = None, capacity: int = 1, clock: Optional[Clock] = None) -> None:
        if rate_per_s is not None and rate_per_s <= 0:
            raise ValueError("rate_per_s must be positive")
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._rate = rate_per_s
        self._capacity = float(capacity)
        self._clock = clock or MonotonicClock()
        self._tokens = float(capacity)
        self._stamp = self._clock.now()
        self._paused_until = 0.0

    def pause(self, seconds: float) -> None:
        self._paused_until = max(self._paused_until, self._clock.now() + max(0.0, seconds))

    async def acquire(self) -> None:
        while True:
            now = self._clock.now()
            if now < self._paused_until:
                await self._clock.sleep(self._paused_until - now)
                continue
            if self._rate is None:
                return
            elapsed = max(0.0, now - self._stamp)
            self._stamp = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            await self._clock.sleep((1.0 - self._tokens) / self._rate)
