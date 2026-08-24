"""
Validation policy for segment request DTOs.
"""
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from src.features.segments.dto import RichSegment


def validate_required_name_policy(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("name is required")
    return value


def validate_required_string_policy(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("field is required")
    return value


def validate_at_least_one_segment_policy(value: "List[RichSegment]") -> "List[RichSegment]":
    if not value:
        raise ValueError("a segment template must contain at least one segment")
    return value
