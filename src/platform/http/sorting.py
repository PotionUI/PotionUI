from enum import Enum
from typing import Optional


class SortDirection(str, Enum):
    asc = "asc"
    desc = "desc"


def resolve_direction(requested: Optional[str], default: str) -> str:
    direction = (requested or default).upper()
    if direction not in ("ASC", "DESC"):
        raise ValueError(f"Invalid sort direction: {requested}")
    return direction
