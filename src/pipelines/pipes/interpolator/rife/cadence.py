from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

_EPSILON = Fraction(1, 10**6)
_SNAP = 1e-3
_DENOMINATOR = 100000


@dataclass(frozen=True)
class Slot:
    index: int
    left: int
    t: float


def source_per_output(src_fps: float, factor: int = 2, target_fps: float = 0.0) -> Fraction:
    if target_fps and target_fps > 0:
        return Fraction(src_fps).limit_denominator(_DENOMINATOR) / Fraction(target_fps).limit_denominator(_DENOMINATOR)
    return Fraction(1, int(factor))


def output_fps(src_fps: float, factor: int = 2, target_fps: float = 0.0) -> float:
    return float(target_fps) if target_fps and target_fps > 0 else float(src_fps) * int(factor)


def output_count(n_source: int, ratio: Fraction) -> int:
    if n_source <= 0:
        return 0
    return max(1, round(Fraction(n_source) / ratio))


def slot(index: int, ratio: Fraction) -> Slot:
    position = index * ratio
    left = math.floor(position + _EPSILON)
    t = float(position - left)
    return Slot(index, left, 0.0 if t < _SNAP else t)
