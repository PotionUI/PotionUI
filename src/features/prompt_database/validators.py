"""
Validation policy for prompt-database request DTOs.
"""
from typing import List

from src.features.segments.dto import RichSegment


def validate_at_least_one_segment_policy(value: List[RichSegment]) -> List[RichSegment]:
    if not value:
        raise ValueError("a prompt must contain at least one segment")
    return value
