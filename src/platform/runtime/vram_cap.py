"""``POTIONUI_VRAM_CAP_GB`` — a debug-only knob that caps the *perceived*
total/free VRAM every placement/admission decision sees.

This exists for the Docker rig-simulation harness (``docker/``): a box with a
large real card still needs onboarding/generation exercised the way a 16GB or
8GB card experiences it. Rather than teach every
VRAM-budgeting call site about a separate "simulated card" concept, this
module is the single seam every raw VRAM read is expected to pass through:
set the env var once, every ``GpuMonitor``/native-engine placement decision
downstream sees a smaller card.

Deliberately narrow:
    - Reads the env var ONCE per process (module-level cache) — this is a
      startup-time simulation knob, not something that changes mid-run.
    - Caps ``total``/``free`` only. ``used`` is left alone: real tensors
      still occupy real VRAM, we're only lying about how much of the card is
      visible to admission decisions.
    - No-ops completely (identity passthrough) when the env var is unset —
      never call this a "regression" on real hardware, it does nothing there.
    - Logs loudly (``logger.warning``, once) when active, so a capped run is
      never mistaken for a real card's numbers in a bug report.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple

from src.platform.observability.logger import logger

VRAM_CAP_ENV_VAR = "POTIONUI_VRAM_CAP_GB"

_BYTES_PER_GB = 1024 ** 3

# Sentinel so "checked the env var and it was unset/invalid" is cached too,
# not just a successfully-parsed cap - avoids re-parsing os.environ (and
# re-logging) on every single VRAM read for the life of the process.
_UNSET = object()
_cached_cap_gb: object = _UNSET


def get_vram_cap_gb() -> Optional[float]:
    """The active VRAM cap in GB, or ``None`` when unset/invalid.

    Parsed from ``POTIONUI_VRAM_CAP_GB`` once per process and cached. Logs a
    loud one-time warning the first time a positive cap is found.
    """
    global _cached_cap_gb
    if _cached_cap_gb is not _UNSET:
        return _cached_cap_gb  # type: ignore[return-value]

    raw = os.environ.get(VRAM_CAP_ENV_VAR)
    if raw is None or not raw.strip():
        _cached_cap_gb = None
        return None

    try:
        cap_gb = float(raw)
    except ValueError:
        logger.warning(
            f"[VRAM_CAP] {VRAM_CAP_ENV_VAR}={raw!r} is not a number; ignoring, "
            f"VRAM will NOT be capped."
        )
        _cached_cap_gb = None
        return None

    if cap_gb <= 0:
        logger.warning(
            f"[VRAM_CAP] {VRAM_CAP_ENV_VAR}={raw!r} must be positive; ignoring, "
            f"VRAM will NOT be capped."
        )
        _cached_cap_gb = None
        return None

    logger.warning(
        f"*** [VRAM_CAP] VRAM capped to {cap_gb:.1f} GB for rig simulation "
        f"({VRAM_CAP_ENV_VAR}={raw!r}). Every placement/admission decision in "
        f"this process now sees a {cap_gb:.1f}GB card, NOT the real one. "
        f"Never mistake this run's numbers for real hardware. ***"
    )
    _cached_cap_gb = cap_gb
    return cap_gb


def apply_vram_cap_bytes(free_bytes: int, total_bytes: int) -> Tuple[int, int]:
    """Cap a raw ``(free, total)`` VRAM reading (bytes) to the env cap.

    ``used`` (``total - free``) is preserved as-is; only how much of the card
    is visible is capped. Identity passthrough when no cap is set.
    """
    cap_gb = get_vram_cap_gb()
    if cap_gb is None:
        return free_bytes, total_bytes

    cap_bytes = int(cap_gb * _BYTES_PER_GB)
    used_bytes = max(0, total_bytes - free_bytes)
    capped_total = min(total_bytes, cap_bytes)
    capped_free = max(0, capped_total - used_bytes)
    return capped_free, capped_total


def apply_vram_cap_gb(free_gb: float, total_gb: float) -> Tuple[float, float]:
    """Same as :func:`apply_vram_cap_bytes` but in GB (float) units."""
    cap_gb = get_vram_cap_gb()
    if cap_gb is None:
        return free_gb, total_gb

    used_gb = max(0.0, total_gb - free_gb)
    capped_total = min(total_gb, cap_gb)
    capped_free = max(0.0, capped_total - used_gb)
    return capped_free, capped_total


def reset_for_tests() -> None:
    """Clear the module-level cache. Test-only — production never needs to
    re-read the env var mid-process."""
    global _cached_cap_gb
    _cached_cap_gb = _UNSET
