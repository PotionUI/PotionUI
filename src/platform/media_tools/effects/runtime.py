from __future__ import annotations

import importlib.util
import logging
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Optional

from src.platform.media_tools.effects.spec import Candidate, EffectSpec
from src.platform.util.video_transcode import find_ffmpeg

logger = logging.getLogger(__name__)

MIN_FREE_VRAM_MB = 1024
PROBE_TIMEOUT_SECONDS = 20.0

GpuPolicy = Callable[[], bool]

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


@dataclass
class EffectRuntime:
    filters: Callable[[], frozenset[str]] = installed_filters
    torch_available: Callable[[], bool] = torch_installed
    gpu_policy: GpuPolicy = field(default=_no_gpu)

    def use_gpu_policy(self, policy: GpuPolicy) -> None:
        self.gpu_policy = policy

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
