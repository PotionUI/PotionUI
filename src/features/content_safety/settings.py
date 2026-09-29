from typing import Any, Optional

from src.features.content_safety.banned_words import validate_entries
from src.features.content_safety.constants import (
    MAX_VIDEO_FRAMES,
    POLICIES,
    SETTING_BANNED_WORDS,
    SETTING_POLICY,
    SETTING_VIDEO_FRAMES,
)


def _whole_number(value: Any, low: int, high: int) -> Optional[str]:
    message = f"must be a whole number between {low} and {high}"
    if isinstance(value, bool):
        return message
    try:
        number = int(value)
    except (TypeError, ValueError):
        return message
    if not low <= number <= high:
        return message
    return None


def validate_setting(key: str, value: Any) -> Optional[str]:
    if key == SETTING_POLICY:
        return None if value in POLICIES else "must be allowed, blur or blocked"
    if key == SETTING_BANNED_WORDS:
        return validate_entries(value)
    if key == SETTING_VIDEO_FRAMES:
        return _whole_number(value, 1, MAX_VIDEO_FRAMES)
    return None
