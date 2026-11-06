from __future__ import annotations

import importlib.util
import os
import logging
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from src.platform.media_tools.effects.looks import Look, LookLibrary
from src.platform.media_tools.effects.spec import Candidate, EffectSpec
from src.platform.util.video_transcode import find_ffmpeg

logger = logging.getLogger(__name__)

MIN_FREE_VRAM_MB = 1024
PROBE_TIMEOUT_SECONDS = 20.0

GpuPolicy = Callable[[], bool]
AssetDirs = Callable[[str], Iterable[Path]]
MAX_LISTED_ASSETS = 500

_FILTER_LINE = re.compile(r"^ [T.][S.][C.] +([A-Za-z0-9_]+) +[AVN|]*->[AVN|]*", re.MULTILINE)


def _no_gpu() -> bool:
    return False


_PROBED: dict[str, frozenset[str]] = {}


def ffmpeg_filters(binary: Optional[str]) -> frozenset[str]:
    if not binary:
        return frozenset()
    if binary in _PROBED:
        return _PROBED[binary]
    try:
        completed = subprocess.run(
            [binary, "-hide_banner", "-filters"],
            capture_output=True, text=True, errors="replace", timeout=PROBE_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not list the filters of %s", binary)
        return frozenset()
    names = parse_filters(completed.stdout) if completed.returncode == 0 else frozenset()
    if names:
        _PROBED[binary] = names
    return names


def parse_filters(text: str) -> frozenset[str]:
    return frozenset(match.group(1) for match in _FILTER_LINE.finditer(text))


def installed_filters() -> frozenset[str]:
    return ffmpeg_filters(find_ffmpeg())


@lru_cache(maxsize=1)
def torch_installed() -> bool:
    return importlib.util.find_spec("torch") is not None


class LifecycleGpuPolicy:
    def __init__(self, lifecycle: Any, min_free_mb: int = MIN_FREE_VRAM_MB):
        self.lifecycle = lifecycle
        self.min_free_mb = min_free_mb

    def __call__(self) -> bool:
        try:
            import torch

            if not torch.cuda.is_available():
                return False
            if self.lifecycle.stats().get("active_leases"):
                return False
            monitor = getattr(self.lifecycle, "gpu_monitor", None)
            free = monitor.get_free_vram() if monitor is not None else None
        except Exception:
            logger.debug("GPU policy check failed; effects stay on the CPU", exc_info=True)
            return False
        return free is not None and free >= self.min_free_mb


@dataclass
class Choice:
    candidate: Candidate
    device: Optional[str] = None


def _contained(base: Path, path: Path) -> Optional[Path]:
    try:
        target = path.resolve()
    except (OSError, RuntimeError):
        return None
    return target if base in target.parents and target.is_file() else None


@dataclass
class EffectRuntime:
    filters: Callable[[], frozenset[str]] = installed_filters
    torch_available: Callable[[], bool] = torch_installed
    gpu_policy: GpuPolicy = field(default=_no_gpu)
    asset_dirs: Optional[AssetDirs] = None
    looks: Optional[LookLibrary] = None

    def use_looks(self, library: LookLibrary) -> None:
        self.looks = library

    def resolve_look(self, ref: str, user_id: Optional[str]) -> Optional[Look]:
        if not ref or self.looks is None:
            return None
        try:
            return self.looks.resolve(ref, user_id)
        except Exception:
            logger.warning("Could not read the look %s", ref, exc_info=True)
            return None

    def list_looks(self, user_id: Optional[str]) -> list[Look]:
        if self.looks is None:
            return []
        try:
            return list(self.looks.list(user_id))
        except Exception:
            logger.warning("Could not list the looks", exc_info=True)
            return []

    def use_gpu_policy(self, policy: GpuPolicy) -> None:
        self.gpu_policy = policy

    def use_asset_dirs(self, dirs: AssetDirs) -> None:
        self.asset_dirs = dirs

    def _dirs(self, kind: str) -> list[Path]:
        if self.asset_dirs is None:
            return []
        try:
            return [Path(path).resolve() for path in self.asset_dirs(kind)]
        except Exception:
            logger.warning("Could not list the %s folders", kind, exc_info=True)
            return []

    def resolve_asset(self, kind: str, ref: str) -> Optional[Path]:
        if not ref:
            return None
        for base in self._dirs(kind):
            lexical = Path(os.path.normpath(base / ref))
            if base not in lexical.parents:
                continue
            target = _contained(base, lexical)
            if target is not None:
                return target
        return None

    def list_assets(self, kind: str, suffix: str) -> list[str]:
        found: set[str] = set()
        for base in self._dirs(kind):
            if not base.is_dir():
                continue
            for path in sorted(base.rglob(f"*{suffix}")):
                if _contained(base, path) is not None:
                    found.add(path.relative_to(base).as_posix())
                if len(found) >= MAX_LISTED_ASSETS:
                    return sorted(found)
        return sorted(found)

    def device(self) -> str:
        try:
            return "cuda" if self.gpu_policy() else "cpu"
        except Exception:
            logger.debug("GPU policy raised; effects stay on the CPU", exc_info=True)
            return "cpu"

    def available(self, candidate: Candidate) -> bool:
        if candidate.kind == "ffmpeg":
            return candidate.template is not None and candidate.template.filters() <= self.filters()
        if candidate.kind == "torch":
            return candidate.function is not None and self.torch_available()
        return candidate.function is not None

    def choose(self, effect: EffectSpec, python_only: bool = False) -> Optional[Choice]:
        for candidate in effect.render:
            if python_only and candidate.kind == "ffmpeg":
                continue
            if self.available(candidate):
                return Choice(candidate, self.device() if candidate.kind == "torch" else None)
        return None


DEFAULT_RUNTIME = EffectRuntime()
