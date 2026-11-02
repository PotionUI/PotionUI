from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from src.platform.media_tools.types import ToolContext, ToolMedia

Memo = Callable[[tuple, Callable[[], Any]], Any]


def no_memo(key: tuple, compute: Callable[[], Any]) -> Any:
    return compute()


@dataclass(frozen=True)
class Clock:
    offset: float = 0.0
    rate: float = 1.0
    length: Optional[float] = None

    def source_time(self, time: float) -> float:
        return self.offset + time * self.rate

    def output_time(self, source: float) -> float:
        return (source - self.offset) / self.rate

    def trimmed(self, start: float, end: Optional[float]) -> "Clock":
        stop = end if self.length is None or end is None else min(end, self.length)
        length = None if stop is None else max(0.0, stop - start)
        return Clock(offset=self.source_time(start), rate=self.rate, length=length)

    def sped(self, speed: float) -> "Clock":
        length = None if self.length is None else self.length / speed
        return Clock(offset=self.offset, rate=self.rate * speed, length=length)

    def source_window(self) -> tuple[float, Optional[float]]:
        end = None if self.length is None else self.source_time(self.length)
        return self.offset, end


@dataclass
class StepContext:
    source: "ToolMedia"
    clock: Clock
    ctx: "ToolContext"
    memo: Memo = field(default=no_memo)
