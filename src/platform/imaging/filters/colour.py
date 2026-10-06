from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Sequence

import numpy as np

ColourMap = Callable[[np.ndarray], np.ndarray]

LUMA = (0.2126, 0.7152, 0.0722)


def luma(c: np.ndarray) -> np.ndarray:
    return LUMA[0] * c[:, 0] + LUMA[1] * c[:, 1] + LUMA[2] * c[:, 2]


def lin(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.04045, v / 12.92, np.power((np.maximum(v, 0.0) + 0.055) / 1.055, 2.4))


def enc(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(np.maximum(v, 0.0), 1 / 2.4) - 0.055)


def smoothstep(a: float, b: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def hue_rgb(hue: float) -> List[float]:
    out = []
    for n in (0, 8, 4):
        k = math.fmod(n + hue / 30, 12)
        out.append(0.5 - 0.5 * max(-1.0, min(k - 3, 9 - k, 1.0)))
    return out


def pchip(points: Sequence[Sequence[float]]) -> Callable[[np.ndarray], np.ndarray]:
    n = len(points)
    x = np.array([p[0] for p in points], dtype=np.float64)
    y = np.array([p[1] for p in points], dtype=np.float64)
    h = x[1:] - x[:-1]
    d = (y[1:] - y[:-1]) / h
    m = np.zeros(n)
    if n == 2:
        m[0] = m[1] = d[0]
    else:
        for i in range(1, n - 1):
            if d[i - 1] * d[i] <= 0:
                m[i] = 0
            else:
                w1 = 2 * h[i] + h[i - 1]
                w2 = h[i] + 2 * h[i - 1]
                m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
        m[0] = d[0]
        m[n - 1] = d[n - 2]

    def evaluate(v: np.ndarray) -> np.ndarray:
        idx = np.clip(np.searchsorted(x, v, side="left") - 1, 0, n - 2)
        t = (v - x[idx]) / h[idx]
        t2 = t * t
        t3 = t2 * t
        inside = (
            (2 * t3 - 3 * t2 + 1) * y[idx]
            + (t3 - 2 * t2 + t) * h[idx] * m[idx]
            + (-2 * t3 + 3 * t2) * y[idx + 1]
            + (t3 - t2) * h[idx] * m[idx + 1]
        )
        below = y[0] + m[0] * (v - x[0])
        above = y[n - 1] + m[n - 1] * (v - x[n - 1])
        return np.where(v <= x[0], below, np.where(v >= x[n - 1], above, inside))

    return evaluate


def tone(p: Mapping[str, Any]) -> ColourMap:
    brightness = max(0.0, 1 + p["brightness"] / 100)
    contrast = max(0.0, 1 + p["contrast"] / 100)
    s = max(0.0, 1 + p["saturation"] / 100)
    angle = p["hue"] * math.pi / 180
    cs = math.cos(angle)
    sn = math.sin(angle)
    sat = np.array(
        [
            [0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
            [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
            [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s],
        ]
    )
    hue = np.array(
        [
            [0.213 + cs * 0.787 - sn * 0.213, 0.715 - cs * 0.715 - sn * 0.715, 0.072 - cs * 0.072 + sn * 0.928],
            [0.213 - cs * 0.213 + sn * 0.143, 0.715 + cs * 0.285 + sn * 0.14, 0.072 - cs * 0.072 - sn * 0.283],
            [0.213 - cs * 0.213 - sn * 0.787, 0.715 - cs * 0.715 + sn * 0.715, 0.072 + cs * 0.928 + sn * 0.072],
        ]
    )

    def apply(c: np.ndarray) -> np.ndarray:
        v = np.clip(c * brightness, 0.0, 1.0)
        v = np.clip((v - 0.5) * contrast + 0.5, 0.0, 1.0)
        sr = sat[0, 0] * v[:, 0] + sat[0, 1] * v[:, 1] + sat[0, 2] * v[:, 2]
        sg = sat[1, 0] * v[:, 0] + sat[1, 1] * v[:, 1] + sat[1, 2] * v[:, 2]
        sb = sat[2, 0] * v[:, 0] + sat[2, 1] * v[:, 1] + sat[2, 2] * v[:, 2]
        out = np.empty_like(v)
        for row in range(3):
            out[:, row] = hue[row, 0] * sr + hue[row, 1] * sg + hue[row, 2] * sb
        return out

    return apply


def exposure(p: Mapping[str, Any]) -> ColourMap:
    gain = math.pow(2, p["stops"])
    return lambda c: enc(lin(c) * gain)


def white_balance(p: Mapping[str, Any]) -> ColourMap:
    t = p["temperature"] / 100
    n = p["tint"] / 100
    gr = math.pow(2, 0.35 * t) * math.pow(2, 0.125 * n)
    gg = math.pow(2, -0.25 * n)
    gb = math.pow(2, -0.35 * t) * math.pow(2, 0.125 * n)
    norm = LUMA[0] * gr + LUMA[1] * gg + LUMA[2] * gb
    gains = np.array([gr / norm, gg / norm, gb / norm])
    return lambda c: enc(lin(c) * gains)


def curves(p: Mapping[str, Any]) -> ColourMap:
    master = pchip(p["master"])
    red = pchip(p["r"])
    green = pchip(p["g"])
    blue = pchip(p["b"])

    def apply(c: np.ndarray) -> np.ndarray:
        out = np.empty_like(c)
        out[:, 0] = master(np.clip(red(c[:, 0]), 0.0, 1.0))
        out[:, 1] = master(np.clip(green(c[:, 1]), 0.0, 1.0))
        out[:, 2] = master(np.clip(blue(c[:, 2]), 0.0, 1.0))
        return out

    return apply


def vibrance(p: Mapping[str, Any]) -> ColourMap:
    a = p["amount"] / 100

    def apply(c: np.ndarray) -> np.ndarray:
        y = luma(c)[:, None]
        sat = (c.max(axis=1) - c.min(axis=1))[:, None]
        f = np.maximum(0.0, 1 + a * (1 - sat))
        return y + (c - y) * f

    return apply


def split_tone(p: Mapping[str, Any]) -> ColourMap:
    hs = np.array(hue_rgb(p["shadow_hue"]))
    hh = np.array(hue_rgb(p["highlight_hue"]))
    ys = LUMA[0] * hs[0] + LUMA[1] * hs[1] + LUMA[2] * hs[2]
    yh = LUMA[0] * hh[0] + LUMA[1] * hh[1] + LUMA[2] * hh[2]
    ss = p["shadow_sat"] / 100
    sh = p["highlight_sat"] / 100
    balance = p["balance"] / 200

    def apply(c: np.ndarray) -> np.ndarray:
        y = luma(c)
        wh = smoothstep(0.0, 1.0, np.clip(y - balance, 0.0, 1.0))[:, None]
        ws = 1 - wh
        return c + 0.5 * (ws * ss * (hs - ys) + wh * sh * (hh - yh))

    return apply


def fade(p: Mapping[str, Any]) -> ColourMap:
    lift = p["black_lift"] / 100
    cap = p["white_cap"] / 100
    return lambda c: lift + c * (1 - lift - cap)


def grayscale(p: Mapping[str, Any]) -> ColourMap:
    return lambda c: np.repeat(luma(c)[:, None], 3, axis=1)


def invert(p: Mapping[str, Any]) -> ColourMap:
    return lambda c: 1 - c


COLOUR_OPS: Dict[str, Callable[[Mapping[str, Any]], ColourMap]] = {
    "tone": tone,
    "exposure": exposure,
    "white_balance": white_balance,
    "curves": curves,
    "vibrance": vibrance,
    "split_tone": split_tone,
    "fade": fade,
    "grayscale": grayscale,
    "invert": invert,
}
