from collections import defaultdict, deque
import os
import time
from typing import Deque, Dict

from fastapi import HTTPException, status


class LoginAttemptLimiter:
    def __init__(self, detail: str = "Too many login attempts. Please try again later.") -> None:
        self.max_attempts = int(os.environ.get("POTIONUI_AUTH_LOGIN_ATTEMPTS", "5"))
        self.window_seconds = int(os.environ.get("POTIONUI_AUTH_LOGIN_WINDOW_SECONDS", "60"))
        self._detail = detail
        self._attempts: Dict[str, Deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> Deque[float]:
        attempts = self._attempts[key]
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return attempts

    def check(self, key: str) -> None:
        if len(self._prune(key, time.monotonic())) >= self.max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=self._detail,
                headers={"Retry-After": str(self.window_seconds)},
            )

    def record_failure(self, key: str) -> None:
        self._prune(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)
