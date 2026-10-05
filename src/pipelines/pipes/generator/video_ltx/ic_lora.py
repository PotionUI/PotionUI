from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple

from safetensors import safe_open

from src.pipelines.contracts import logger

DOWNSCALE_KEY = "reference_downscale_factor"
TEMPORAL_KEY = "reference_temporal_scale_factor"


def _factor(metadata: dict, key: str, path: str) -> int:
    raw = metadata.get(key)
    if raw is None:
        return 1
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        logger.warning("[IC-LORA] %s: ignoring unreadable %s=%r", Path(path).name, key, raw)
        return 1
    return value if value >= 1 else 1


def read_reference_scales(path: str) -> Tuple[int, int]:
    try:
        with safe_open(str(path), framework="pt") as handle:
            metadata = handle.metadata() or {}
    except Exception as exc:
        logger.warning("[IC-LORA] %s: cannot read safetensors metadata (%s), assuming scale 1",
                       Path(str(path)).name, exc)
        return 1, 1
    return _factor(metadata, DOWNSCALE_KEY, str(path)), _factor(metadata, TEMPORAL_KEY, str(path))


def resolve_reference_scales(paths: Iterable[str]) -> Tuple[int, int]:
    spatial, temporal = 1, 1
    for path in paths:
        s, t = read_reference_scales(path)
        if s != 1:
            if spatial not in (1, s):
                raise ValueError(
                    f"IC-LoRAs disagree on {DOWNSCALE_KEY}: {spatial} vs {s} ({Path(str(path)).name}); "
                    f"LoRAs trained at different reference scales cannot be combined")
            spatial = s
        if t != 1:
            if temporal not in (1, t):
                raise ValueError(
                    f"IC-LoRAs disagree on {TEMPORAL_KEY}: {temporal} vs {t} ({Path(str(path)).name}); "
                    f"LoRAs trained at different reference scales cannot be combined")
            temporal = t
    return spatial, temporal
