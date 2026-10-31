from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LUMA_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float64)
GRAY_LOW = 0.06
GRAY_HIGH = 0.94
MIN_GAIN = 0.6
MAX_GAIN = 1.6
BLACK_PERCENTILE = 0.5
WHITE_PERCENTILE = 99.5
MAX_BLACK = 0.25
MIN_WHITE = 0.75
NEGLIGIBLE = 0.004
MIN_SPREAD = 0.1


@dataclass(frozen=True)
class ColorFix:
    gains: tuple[float, float, float] = (1.0, 1.0, 1.0)
    black: float = 0.0
    white: float = 1.0

    def is_identity(self) -> bool:
        return (
            all(abs(gain - 1.0) < NEGLIGIBLE for gain in self.gains)
            and self.black < NEGLIGIBLE
            and self.white > 1.0 - NEGLIGIBLE
        )

    def with_strength(self, strength: float) -> "ColorFix":
        amount = min(1.0, max(0.0, strength))
        return ColorFix(
            gains=tuple(1.0 + amount * (gain - 1.0) for gain in self.gains),
            black=self.black * amount,
            white=1.0 - (1.0 - self.white) * amount,
        )


def _pixels(frames: np.ndarray) -> np.ndarray:
    return np.asarray(frames, dtype=np.float64).reshape(-1, 3) / 255.0


def grey_world_gains(frames: np.ndarray) -> tuple[float, float, float]:
    pixels = _pixels(frames)
    luma = pixels @ LUMA_WEIGHTS
    usable = pixels[(luma > GRAY_LOW) & (luma < GRAY_HIGH)]
    if usable.shape[0] < 16:
        return (1.0, 1.0, 1.0)
    means = usable.mean(axis=0)
    if np.any(means <= 1e-6):
        return (1.0, 1.0, 1.0)
    target = float(means @ LUMA_WEIGHTS)
    gains = np.clip(target / means, MIN_GAIN, MAX_GAIN)
    gains = gains / float(gains @ LUMA_WEIGHTS)
    gains = np.clip(gains, MIN_GAIN, MAX_GAIN)
    return (float(gains[0]), float(gains[1]), float(gains[2]))


def apply_gains(frames: np.ndarray, gains: tuple[float, float, float]) -> np.ndarray:
    scaled = np.asarray(frames, dtype=np.float64) * np.array(gains, dtype=np.float64)
    return np.clip(scaled, 0, 255)


def level_points(frames: np.ndarray) -> tuple[float, float]:
    luma = _pixels(frames) @ LUMA_WEIGHTS
    black = float(np.percentile(luma, BLACK_PERCENTILE))
    white = float(np.percentile(luma, WHITE_PERCENTILE))
    if white - black < MIN_SPREAD:
        return (0.0, 1.0)
    black = min(black, MAX_BLACK)
    white = max(white, MIN_WHITE)
    return (black, white)


def estimate_fix(frames: np.ndarray, *, white_balance: bool, contrast: bool) -> ColorFix:
    working = np.asarray(frames, dtype=np.float64)
    gains = (1.0, 1.0, 1.0)
    if white_balance:
        gains = grey_world_gains(working)
        working = apply_gains(working, gains)
    black, white = (0.0, 1.0)
    if contrast:
        black, white = level_points(working)
    return ColorFix(gains=gains, black=black, white=white)


def apply_fix(frames: np.ndarray, fix: ColorFix) -> np.ndarray:
    balanced = apply_gains(frames, fix.gains) / 255.0
    span = max(fix.white - fix.black, 1e-6)
    leveled = np.clip((balanced - fix.black) / span, 0.0, 1.0)
    return np.round(leveled * 255.0).astype(np.uint8)


def ffmpeg_filters(fix: ColorFix) -> list[str]:
    filters: list[str] = []
    if any(abs(gain - 1.0) >= NEGLIGIBLE for gain in fix.gains):
        red, green, blue = fix.gains
        filters.append(f"colorchannelmixer=rr={red:.4f}:gg={green:.4f}:bb={blue:.4f}")
    if fix.black >= NEGLIGIBLE or fix.white <= 1.0 - NEGLIGIBLE:
        low, high = f"{fix.black:.4f}", f"{fix.white:.4f}"
        filters.append(
            f"colorlevels=rimin={low}:gimin={low}:bimin={low}:rimax={high}:gimax={high}:bimax={high}"
        )
    return filters
