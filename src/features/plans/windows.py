import logging
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional, Set, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.features.plans.constants import DEFAULT_TIMEZONE

logger = logging.getLogger(__name__)

_warned: Set[str] = set()


def _load(name: str) -> Optional[tzinfo]:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc if name.strip().upper() == DEFAULT_TIMEZONE else None


def valid_timezone(name: str) -> bool:
    if not isinstance(name, str) or not name.strip():
        return False
    return _load(name) is not None


def zone(name: Optional[str]) -> tzinfo:
    if isinstance(name, str) and name.strip():
        tz = _load(name)
        if tz is not None:
            return tz
        if name not in _warned:
            _warned.add(name)
            logger.warning("Time zone %r is not available here; plan windows use UTC instead", name)
    return _load(DEFAULT_TIMEZONE) or timezone.utc


def zone_key(name: Optional[str]) -> str:
    return getattr(zone(name), "key", DEFAULT_TIMEZONE)


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
    name = zone_key(tz_name)
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
