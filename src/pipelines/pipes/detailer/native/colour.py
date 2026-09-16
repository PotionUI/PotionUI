from __future__ import annotations

import numpy as np

_D65 = np.array([0.95047, 1.00000, 1.08883], dtype=np.float64)
_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)
_XYZ_TO_RGB = np.linalg.inv(_RGB_TO_XYZ)
_DELTA = 6.0 / 29.0


def _signed_power(values: np.ndarray, exponent: float) -> np.ndarray:
    return np.sign(values) * np.abs(values) ** exponent


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    values = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(
        values <= 0.04045,
        values / 12.92,
        _signed_power((np.abs(values) + 0.055) / 1.055, 2.4),
    )
    xyz = linear @ _RGB_TO_XYZ.T / _D65
    f = np.where(xyz > _DELTA ** 3, _signed_power(xyz, 1.0 / 3.0), xyz / (3 * _DELTA ** 2) + 4.0 / 29.0)
    return np.stack(
        [116.0 * f[..., 1] - 16.0, 500.0 * (f[..., 0] - f[..., 1]), 200.0 * (f[..., 1] - f[..., 2])],
        axis=-1,
    )


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16.0) / 116.0
    fx = fy + lab[..., 1] / 500.0
    fz = fy - lab[..., 2] / 200.0
    f = np.stack([fx, fy, fz], axis=-1)
    xyz = np.where(f > _DELTA, _signed_power(f, 3.0), 3 * _DELTA ** 2 * (f - 4.0 / 29.0)) * _D65
    linear = xyz @ _XYZ_TO_RGB.T
    values = np.where(
        linear <= 0.0031308,
        linear * 12.92,
        1.055 * _signed_power(np.abs(linear), 1.0 / 2.4) - 0.055,
    )
    return values * 255.0


def weighted_stats(values: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    w = np.asarray(weights, dtype=np.float64)[..., None]
    total = float(w.sum())
    if total <= 0.0:
        channels = values.shape[-1]
        return np.zeros(channels), np.ones(channels)
    mean = (values * w).sum(axis=(0, 1)) / total
    variance = (((values - mean) ** 2) * w).sum(axis=(0, 1)) / total
    return mean, np.sqrt(np.maximum(variance, 0.0))


def match_colour(refined: np.ndarray, original: np.ndarray, weights: np.ndarray) -> np.ndarray:
    weights = np.asarray(weights, dtype=np.float64)
    if float(weights.sum()) <= 0.0:
        return np.asarray(refined, dtype=np.float64)

    refined_lab = rgb_to_lab(refined)
    original_lab = rgb_to_lab(original)
    refined_mean, refined_std = weighted_stats(refined_lab, weights)
    original_mean, original_std = weighted_stats(original_lab, weights)

    scale = np.where(refined_std > 1e-6, original_std / np.maximum(refined_std, 1e-6), 1.0)
    corrected = lab_to_rgb((refined_lab - refined_mean) * scale + original_mean)
    base = np.asarray(refined, dtype=np.float64)
    return base + weights[..., None] * (corrected - base)
