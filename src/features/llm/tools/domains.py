from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DomainServices:
    automation: Any = None
    library: Any = None
    sessions: Any = None
    notifications: Any = None
    inspirations: Any = None
    downloads: Any = None
    elixirs: Any = None
