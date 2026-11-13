import threading
from typing import Callable, Optional


class PreviewSlots:
    def __init__(self, limit: Callable[[], int]):
        self._limit = limit
        self._condition = threading.Condition()
        self._in_use = 0

    @property
    def in_use(self) -> int:
        with self._condition:
            return self._in_use

    def acquire(self, timeout: Optional[float] = None) -> bool:
        with self._condition:
            acquired = self._condition.wait_for(lambda: self._in_use < max(1, self._limit()), timeout)
            if acquired:
                self._in_use += 1
            return acquired

    def release(self) -> None:
        with self._condition:
            self._in_use = max(0, self._in_use - 1)
            self._condition.notify_all()
