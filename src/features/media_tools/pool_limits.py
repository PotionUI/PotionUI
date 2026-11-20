import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

KEY_MAX_CONCURRENT = "media_tools_max_concurrent"
KEY_MAX_PREVIEWS = "media_tools_max_previews"
KEY_MAX_RUNNING_PER_USER = "media_tools_max_running_per_user"
KEY_MAX_QUEUED_PER_USER = "media_tools_max_queued_per_user"
KEY_FFMPEG_THREADS = "media_tools_ffmpeg_threads"

SETTING_DEFAULTS = {
    KEY_MAX_CONCURRENT: 0,
    KEY_MAX_PREVIEWS: 2,
    KEY_MAX_RUNNING_PER_USER: 0,
    KEY_MAX_QUEUED_PER_USER: 8,
    KEY_FFMPEG_THREADS: 0,
}

POOL_SETTING_KEYS = {
    "max_concurrent": KEY_MAX_CONCURRENT,
    "max_previews": KEY_MAX_PREVIEWS,
    "max_running_per_user": KEY_MAX_RUNNING_PER_USER,
    "max_queued_per_user": KEY_MAX_QUEUED_PER_USER,
    "ffmpeg_threads": KEY_FFMPEG_THREADS,
}

MIN_AUTO_CONCURRENT = 2
MAX_AUTO_CONCURRENT = 8
CORES_PER_AUTO_RUN = 4
HARD_MAX = 256
CACHE_SECONDS = 2.0


@dataclass(frozen=True)
class PoolLimits:
    max_concurrent: int = 2
    max_previews: int = 2
    max_running_per_user: int = 0
    max_queued_per_user: int = 8
    ffmpeg_threads: int = 0


def cpu_cores() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def auto_max_concurrent(cores: int) -> int:
    return max(MIN_AUTO_CONCURRENT, min(MAX_AUTO_CONCURRENT, cores // CORES_PER_AUTO_RUN))


def auto_ffmpeg_threads(cores: int, max_concurrent: int) -> int:
    return max(1, cores // max(1, max_concurrent))


def _integer(value: Any, default: int, minimum: int, maximum: int = HARD_MAX) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def resolve_limits(read: Callable[[str], Any], cores: int) -> PoolLimits:
    configured = _integer(read(KEY_MAX_CONCURRENT), 0, 0)
    max_concurrent = configured if configured > 0 else auto_max_concurrent(cores)
    threads = _integer(read(KEY_FFMPEG_THREADS), 0, 0)
    return PoolLimits(
        max_concurrent=max_concurrent,
        max_previews=_integer(read(KEY_MAX_PREVIEWS), SETTING_DEFAULTS[KEY_MAX_PREVIEWS], 1),
        max_running_per_user=_integer(read(KEY_MAX_RUNNING_PER_USER), 0, 0),
        max_queued_per_user=_integer(read(KEY_MAX_QUEUED_PER_USER), SETTING_DEFAULTS[KEY_MAX_QUEUED_PER_USER], 1),
        ffmpeg_threads=threads if threads > 0 else auto_ffmpeg_threads(cores, max_concurrent),
    )


class SettingsLimits:
    def __init__(
        self,
        settings: Any,
        cores: Optional[int] = None,
        clock: Callable[[], float] = time.monotonic,
        cache_seconds: float = CACHE_SECONDS,
    ):
        self._settings = settings
        self._cores = cores if cores is not None else cpu_cores()
        self._clock = clock
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._value: Optional[PoolLimits] = None
        self._read_at = 0.0

    def __call__(self) -> PoolLimits:
        with self._lock:
            now = self._clock()
            if self._value is not None and now - self._read_at < self._cache_seconds:
                return self._value
            try:
                value = resolve_limits(
                    lambda key: self._settings.get_setting(key, SETTING_DEFAULTS[key]), self._cores
                )
            except Exception:
                logger.exception("The media tool limits could not be read; keeping the last known values")
                value = self._value if self._value is not None else resolve_limits(
                    lambda key: SETTING_DEFAULTS[key], self._cores
                )
            self._value = value
            self._read_at = now
            return value

    def invalidate(self) -> None:
        with self._lock:
            self._value = None
