"""Mirrors NativeBackend.prepare_pipes' device/dtype/vram injection, worker-side.

A package's processed pipe config already carries every pipe-class default
(``src.features.generation.effective_config.merge_pipe_defaults`` ran on the
dispatching side) except ``device``/``dtype``/``vram_limit_gb`` - core
deliberately leaves those three for whichever compute host actually executes
the pipeline to decide, exactly as ``NativeBackend.prepare_pipes`` decides them
for a local generation. ``setdefault`` semantics are the load-bearing part: a
preset that set one of these explicitly on a pipe still wins.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: The keys NativeBackend.prepare_pipes injects, in the order it injects them.
INJECTED_KEYS = ("device", "dtype", "vram_limit_gb")


def inject_worker_device(
    config: Dict[str, Any],
    *,
    device: str,
    dtype: str,
    vram_limit_gb: Optional[float],
) -> Dict[str, Any]:
    """Return a copy of *config* with this worker's device/dtype/vram
    setdefault'd in. *config* itself is never mutated."""
    injected = dict(config)
    for key, value in (
        ("device", device),
        ("dtype", dtype),
        ("vram_limit_gb", vram_limit_gb),
    ):
        injected.setdefault(key, value)
    return injected
