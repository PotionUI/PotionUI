from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class SetupCheckContext:
    plugin_id: str
    settings: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SetupCheckResult:
    done: bool
    description: str = ""
    action_label: Optional[str] = None
    action_href: Optional[str] = None


class SetupCheck(ABC):
    @abstractmethod
    def evaluate(self, context: SetupCheckContext) -> SetupCheckResult: ...
