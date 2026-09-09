"""The three retention windows the housekeeping worker applies."""

from typing import Any, Dict, Optional

SETTING_TMP_DAYS = "tmp_retention_days"
SETTING_RUN_REPORT_DAYS = "run_report_retention_days"
SETTING_LLM_TRACE_DAYS = "llm_trace_retention_days"

DEFAULTS = {
    SETTING_TMP_DAYS: 7,
    SETTING_RUN_REPORT_DAYS: 30,
    SETTING_LLM_TRACE_DAYS: 7,
}

MIN_DAYS = 0
MAX_DAYS = 3650


def validate_setting(key: str, value: Any) -> Optional[str]:
    """The reason `value` is not acceptable for retention setting `key`, or
    None. Returns None for any key this module does not own."""
    if key not in DEFAULTS:
        return None
    if isinstance(value, bool):
        return f"must be a whole number of days between {MIN_DAYS} and {MAX_DAYS}"
    try:
        days = int(value)
    except (TypeError, ValueError):
        return f"must be a whole number of days between {MIN_DAYS} and {MAX_DAYS}"
    if not MIN_DAYS <= days <= MAX_DAYS:
        return f"must be between {MIN_DAYS} and {MAX_DAYS}"
    return None


def _days(settings, key: str) -> int:
    value = settings.get_setting(key, DEFAULTS[key])
    if isinstance(value, bool):
        return DEFAULTS[key]
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULTS[key]
    return max(MIN_DAYS, min(MAX_DAYS, days))


def load_retention(settings) -> Dict[str, int]:
    """Every retention window, clamped to the range the API accepts."""
    return {key: _days(settings, key) for key in DEFAULTS}
