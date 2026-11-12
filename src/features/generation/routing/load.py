from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Dict, Tuple


@dataclass(frozen=True)
class BackendLoad:
    queued: int
    running: int
    reserved: int
    capacity: int

    @property
    def total(self) -> int:
        return self.queued + self.running + self.reserved

    @property
    def weighted(self) -> Fraction:
        return Fraction(self.total, max(1, self.capacity))

    def describe(self) -> str:
        return f"{self.total} queued or running on {self.capacity} slot(s)"


class BackendLoadLedger:
    def __init__(
        self,
        counts: Callable[[str], Tuple[int, int]],
        capacity: Callable[[str], int],
    ):
        self._counts = counts
        self._capacity = capacity
        self._reserved: Dict[str, int] = {}

    def load(self, backend_id: str) -> BackendLoad:
        queued, running = self._counts(backend_id)
        return BackendLoad(
            queued=queued,
            running=running,
            reserved=self._reserved.get(backend_id, 0),
            capacity=max(1, self._capacity(backend_id)),
        )

    def reserve(self, backend_id: str) -> None:
        self._reserved[backend_id] = self._reserved.get(backend_id, 0) + 1

    def release(self, backend_id: str) -> None:
        remaining = self._reserved.get(backend_id, 0) - 1
        if remaining > 0:
            self._reserved[backend_id] = remaining
        else:
            self._reserved.pop(backend_id, None)
