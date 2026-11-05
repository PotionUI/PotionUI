"""Cgroup-v2-aware system RAM reading.

``psutil.virtual_memory()`` reports the HOST's RAM — even from inside a
memory-limited container (``docker run --memory``, a k8s
``resources.limits.memory``, ...).

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

DEFAULT_CGROUP_ROOT = Path("/sys/fs/cgroup")
DEFAULT_PROC_CGROUP_PATH = Path("/proc/self/cgroup")

_UNLIMITED = "max"

# Logged once per process the first time a real cgroup limit is found, so a
# capped container run is visible in logs without spamming every call site.
_warned_once = False

_warned_unreadable_stat: set[Path] = set()


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


def _inactive_file_bytes(stat_path: Path) -> int | None:
    try:
        lines = stat_path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        name, _, value = line.partition(" ")
        if name == "inactive_file":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _used_bytes(folder: Path, usage: int) -> int:
    stat_path = folder / "memory.stat"
    inactive = _inactive_file_bytes(stat_path)
    if inactive is None:
        if stat_path not in _warned_unreadable_stat:
            _warned_unreadable_stat.add(stat_path)
            logger.warning(
                "[SYSTEM_MEMORY] no usable inactive_file in %s: "
                "this cgroup's page cache counts as used RAM",
                stat_path,
            )
        return usage
    return max(0, usage - inactive)


def _own_cgroup(proc_cgroup_path: Path) -> str | None:
    try:
        lines = proc_cgroup_path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("0::"):
            return line[3:]
    return None


def _levels(mount: Path, own: str | None) -> list[Path]:
    levels = [mount]
    parts = [part for part in (own or "").split("/") if part]
    if ".." in parts:
        return levels
    for depth in range(1, len(parts) + 1):
        folder = mount.joinpath(*parts[:depth])
        levels.append(folder)
    return levels


def _tightest(levels: list[Path], available: int) -> tuple[int, int] | None:
    found: tuple[int, int] | None = None
    for folder in levels:
        limit = _read_int_file(folder / "memory.max")
        if limit is None:
            continue
        usage = _read_int_file(folder / "memory.current")
        headroom = available if usage is None else max(0, limit - _used_bytes(folder, usage))
        if found is None:
            found = (limit, headroom)
        else:
            found = (min(found[0], limit), min(found[1], headroom))
    return found


def get_system_memory(
    *,
    cgroup_root: Path = DEFAULT_CGROUP_ROOT,
    proc_cgroup_path: Path = DEFAULT_PROC_CGROUP_PATH,
) -> SystemMemory:
    vm = psutil.virtual_memory()
    total = vm.total
    available = vm.available

    found = _tightest(_levels(cgroup_root, _own_cgroup(proc_cgroup_path)), available)
    if found is None:
        return SystemMemory(total=total, available=available)

    limit, headroom = found
    capped_total = min(total, limit)
    capped_available = min(headroom, capped_total, available)

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
