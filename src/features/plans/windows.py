from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.features.plans.constants import DEFAULT_TIMEZONE


def valid_timezone(name: str) -> bool:
    if not isinstance(name, str) or not name.strip():
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def zone(name: Optional[str]) -> ZoneInfo:
    return ZoneInfo(name) if name and valid_timezone(name) else ZoneInfo(DEFAULT_TIMEZONE)


def window_bounds(window: str, tz_name: Optional[str], now: datetime) -> Tuple[Optional[datetime], Optional[datetime]]:
    if window == "none":
        return None, None
    tz = zone(tz_name)
    local = now.astimezone(tz)
    if window == "day":
        start = datetime(local.year, local.month, local.day, tzinfo=tz)
        nxt = start.date() + timedelta(days=1)
        end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    else:
        start = datetime(local.year, local.month, 1, tzinfo=tz)
        year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
        end = datetime(year, month, 1, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def reset_label(window: str, tz_name: Optional[str]) -> str:
    name = zone(tz_name).key
    if window == "day":
        return f"Resets at 00:00 {name}"
    if window == "month":
        return f"Resets on the 1st, 00:00 {name}"
    return ""


def until_text(now: datetime, then: datetime) -> str:
    minutes = max(0, int((then - now).total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {minutes} min"
    return f"{max(minutes, 1)} min"


def local_day_text(then: datetime, tz_name: Optional[str]) -> str:
    local = then.astimezone(zone(tz_name))
    return f"{local.strftime('%b')} {local.day}"
