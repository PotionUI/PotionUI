"""Cgroup-v2-aware system RAM reading.

``psutil.virtual_memory()`` reports the HOST's RAM — even from inside a
memory-limited container (``docker run --memory``, a k8s
``resources.limits.memory``, ...). Every admission decision in this codebase
that budgets against "how much system RAM is there" (model-lifecycle LRU
eviction headroom, standalone-upscale RAM floor, ...) reads through
``psutil.virtual_memory()`` directly, so inside a memory-limited container
those decisions overshoot the real ceiling: they keep loading/caching until
the HOST looks full, at which point the container's cgroup — not the
model-lifecycle admission control — is what stops it, via the OOM killer.

This is exactly the failure mode the Docker rig-simulation harness
(``docker/``) exists to catch, and it is a real bug on the eventual 0.0.2
Docker distribution too, not just a test aid: any user running PotionUI in a
memory-capped container hits it.

``get_system_memory()`` is the one shared seam: it returns ``psutil``'s
reading clamped to the cgroup v2 limit (``/sys/fs/cgroup/memory.max`` +
``memory.current``) when one is present, and is a plain passthrough
otherwise (no cgroup v2, or an unlimited ``"max"`` limit) — call sites that
route through it are unaffected outside a memory-limited container.

Cgroup v1 is deliberately out of scope: v2 is the default (and effectively
universal) on every distro Docker/containerd ship today.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil

logger = logging.getLogger(__name__)

DEFAULT_CGROUP_MEMORY_MAX_PATH = Path("/sys/fs/cgroup/memory.max")
DEFAULT_CGROUP_MEMORY_CURRENT_PATH = Path("/sys/fs/cgroup/memory.current")

_UNLIMITED = "max"

# Logged once per process the first time a real cgroup limit is found, so a
# capped container run is visible in logs without spamming every call site.
_warned_once = False


@dataclass(frozen=True)
class SystemMemory:
    """Bytes, matching ``psutil.virtual_memory()``'s ``total``/``available``."""

    total: int
    available: int

    @property
    def total_gb(self) -> float:
        return self.total / (1024 ** 3)

    @property
    def available_gb(self) -> float:
        return self.available / (1024 ** 3)


def _read_int_file(path: Path) -> Optional[int]:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    if raw == _UNLIMITED:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def get_system_memory(
    *,
    cgroup_max_path: Path = DEFAULT_CGROUP_MEMORY_MAX_PATH,
    cgroup_current_path: Path = DEFAULT_CGROUP_MEMORY_CURRENT_PATH,
) -> SystemMemory:
    """``(total, available)`` system RAM in bytes, cgroup-v2-aware.

    - No cgroup v2 limit visible (path missing/unreadable, or the limit is
      the literal string ``"max"``/unlimited) -> returns
      ``psutil.virtual_memory()``'s ``total``/``available`` unchanged.
    - A numeric cgroup v2 limit is present -> ``total`` is clamped to that
      limit, and ``available`` is clamped to ``limit - memory.current``
      (the cgroup's own live usage counter) when readable, else to
      ``min(psutil available, capped total)``. Either way the result is
      never larger than what psutil itself reported.
    """
    vm = psutil.virtual_memory()
    total = vm.total
    available = vm.available

    limit = _read_int_file(cgroup_max_path)
    if limit is None:
        return SystemMemory(total=total, available=available)

    capped_total = min(total, limit)
    current = _read_int_file(cgroup_current_path)
    if current is not None:
        capped_available = max(0, limit - current)
    else:
        capped_available = available
    capped_available = min(capped_available, capped_total, available)

    global _warned_once
    if not _warned_once:
        logger.warning(
            "[SYSTEM_MEMORY] cgroup v2 memory limit detected: %.2fGB "
            "(host RAM: %.2fGB) - RAM-budgeting decisions will use the "
            "container limit, not host RAM",
            limit / (1024 ** 3),
            total / (1024 ** 3),
        )
        _warned_once = True

    return SystemMemory(total=capped_total, available=capped_available)


def reset_warning_for_tests() -> None:
    """Clear the one-time-warning flag. Test-only."""
    global _warned_once
    _warned_once = False
