import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.platform.imaging.filters import (
    CORE_OPS,
    apply_filter,
    compile_lut,
    grain_noise,
    lowbias32,
    parse_cube,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
BUILTIN_DIR = ROOT / "content" / "filters" / "marketplace"
WIDTH = 24
HEIGHT = 16
SAMPLE_COUNT = 64

SPECIAL_PIXELS = [
    (0, 0, 0), (255, 255, 255), (128, 128, 128), (255, 0, 0), (0, 255, 0), (0, 0, 255),
    (224, 172, 105), (141, 85, 36), (64, 64, 64), (192, 192, 192), (255, 128, 0), (10, 200, 250),
    (250, 250, 5), (30, 20, 90), (100, 150, 200), (7, 7, 7),
]


def image_pixels() -> List[int]:
    pixels: List[int] = []
    for pixel in SPECIAL_PIXELS:
        pixels.extend(pixel)
    state = 12345
    while len(pixels) < WIDTH * HEIGHT * 3:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        pixels.append((state >> 16) & 0xFF)
    return pixels


def image_array() -> np.ndarray:
    return np.array(image_pixels(), dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)


def flat(array: np.ndarray) -> List[int]:
    return [int(v) for v in array.reshape(-1)]


def lut_samples(steps, cube=None, intensity=100) -> Dict[str, Any]:
    lut = compile_lut(steps, cube, intensity)
    count = lut.data.shape[0]
    indices = [(i * 569) % count for i in range(SAMPLE_COUNT)]
    return {
        "size": lut.size,
        "indices": indices,
        "values": [[float(v) for v in lut.data[i]] for i in indices],
    }


def render(steps, intensity, cube=None) -> List[int]:
    return flat(apply_filter(image_array(), steps, intensity, cube=cube))


def op_cases() -> List[Dict[str, Any]]:
    S = [[0, 0], [0.25, 0.15], [0.75, 0.85], [1, 1]]
    definitions = [
        ("tone-brightness", [{"op": "tone", "brightness": 30}], 100),
        ("tone-brightness-down", [{"op": "tone", "brightness": -45}], 100),
        ("tone-contrast", [{"op": "tone", "contrast": -40}], 100),
        ("tone-contrast-up", [{"op": "tone", "contrast": 35}], 100),
        ("tone-saturation", [{"op": "tone", "saturation": 50}], 100),
        ("tone-desaturate", [{"op": "tone", "saturation": -80}], 100),
        ("tone-hue", [{"op": "tone", "hue": 90}], 100),
        ("tone-hue-negative", [{"op": "tone", "hue": -150}], 100),
        ("tone-combined", [{"op": "tone", "brightness": 6, "contrast": 12, "saturation": 18, "hue": 20}], 100),
        ("exposure-up", [{"op": "exposure", "stops": 1}], 100),
        ("exposure-down", [{"op": "exposure", "stops": -1.5}], 100),
        ("white-balance-warm", [{"op": "white_balance", "temperature": 40}], 100),
        ("white-balance-cool-tint", [{"op": "white_balance", "temperature": -30, "tint": 20}], 100),
        ("curves-master", [{"op": "curves", "master": S}], 100),
        ("curves-channels", [{"op": "curves", "r": [[0, 0], [0.5, 0.4], [1, 1]], "g": [[0, 0.05], [1, 0.95]], "b": S}], 100),
        ("curves-two-points", [{"op": "curves", "master": [[0, 0.1], [1, 0.8]]}], 100),
        ("curves-interior-flat", [{"op": "curves", "master": [[0, 0], [0.3, 0.6], [0.6, 0.6], [1, 1]]}], 100),
        ("vibrance-up", [{"op": "vibrance", "amount": 60}], 100),
        ("vibrance-down", [{"op": "vibrance", "amount": -50}], 100),
        ("split-tone", [{"op": "split_tone", "shadow_hue": 210, "shadow_sat": 50, "highlight_hue": 40, "highlight_sat": 40}], 100),
        ("split-tone-balance", [{"op": "split_tone", "shadow_hue": 300, "shadow_sat": 60, "highlight_hue": 120, "highlight_sat": 30, "balance": -40}], 100),
        ("split-tone-balance-up", [{"op": "split_tone", "shadow_hue": 10, "shadow_sat": 30, "highlight_hue": 330, "highlight_sat": 70, "balance": 50}], 100),
        ("fade", [{"op": "fade", "black_lift": 10, "white_cap": 5}], 100),
        ("grayscale", [{"op": "grayscale"}], 100),
        ("invert", [{"op": "invert"}], 100),
        ("vignette-dark", [{"op": "vignette", "amount": 50, "midpoint": 30, "feather": 50}], 100),
        ("vignette-light", [{"op": "vignette", "amount": -60, "midpoint": 40, "feather": 20}], 100),
        ("vignette-feather-zero", [{"op": "vignette", "amount": 70, "midpoint": 30, "feather": 0}], 100),
        ("vignette-intensity", [{"op": "vignette", "amount": 80}], 40),
        ("grain", [{"op": "grain", "amount": 60, "size": 1, "seed": 9}], 100),
        ("grain-intensity", [{"op": "grain", "amount": 80, "size": 2, "seed": 65535}], 35),
        ("disabled-step", [{"op": "invert", "enabled": False}, {"op": "tone", "brightness": 10}], 100),
        ("step-order", [{"op": "tone", "contrast": 20}, {"op": "white_balance", "temperature": 20}, {"op": "fade", "black_lift": 5}], 100),
        ("intensity-half", [{"op": "tone", "contrast": 40, "saturation": 30}, {"op": "vignette", "amount": 40}], 50),
    ]
    cases = []
    for name, steps, intensity in definitions:
        case: Dict[str, Any] = {
            "name": name,
            "steps": steps,
            "intensity": intensity,
            "expected": render(steps, intensity),
        }
        if all(CORE_OPS[s["op"]].kind == "colour" for s in steps):
            case["lut_samples"] = lut_samples(steps, None, intensity)
        cases.append(case)
    return cases


def filter_cases() -> List[Dict[str, Any]]:
    cases = []
    for directory in sorted(BUILTIN_DIR.iterdir()):
        data = yaml.safe_load((directory / "filter.yml").read_text(encoding="utf-8"))
        for intensity in (0, 50, 100):
            cases.append(
                {
                    "name": f"{data['id']}-{intensity}",
                    "filter": data["id"],
                    "steps": data["steps"],
                    "intensity": intensity,
                    "expected": render(data["steps"], intensity),
                    "lut_samples": lut_samples(
                        [s for s in data["steps"] if CORE_OPS[s["op"]].kind == "colour"], None, intensity
                    ),
                }
            )
    return cases


def spatial_cases() -> List[Dict[str, Any]]:
    definitions = [
        ("vignette-corner", 200, 120, (120, 90, 60), [{"op": "vignette", "amount": 60, "midpoint": 40, "feather": 60}], 100, (0, 0, 16, 16)),
        ("vignette-wide-edge", 400, 240, (200, 200, 200), [{"op": "vignette", "amount": -50, "midpoint": 50, "feather": 30}], 70, (360, 100, 40, 12)),
        ("vignette-resolution-small", 100, 60, (120, 90, 60), [{"op": "vignette", "amount": 60, "midpoint": 40, "feather": 60}], 100, (0, 0, 8, 8)),
        ("grain-cell-two", 400, 400, (128, 128, 128), [{"op": "grain", "amount": 70, "size": 4, "seed": 11}], 100, (100, 100, 24, 24)),
        ("grain-cell-half-up", 625, 625, (90, 160, 210), [{"op": "grain", "amount": 50, "size": 4, "seed": 3}], 100, (300, 300, 20, 20)),
        ("grain-cell-one", 500, 300, (200, 60, 60), [{"op": "grain", "amount": 100, "size": 2, "seed": 0}], 60, (10, 10, 24, 24)),
        ("grain-then-vignette", 300, 300, (180, 150, 120), [{"op": "grain", "amount": 40, "size": 1, "seed": 5}, {"op": "vignette", "amount": 50}], 100, (0, 0, 20, 20)),
    ]
    cases = []
    for name, width, height, fill, steps, intensity, crop in definitions:
        image = np.empty((height, width, 3), dtype=np.uint8)
        image[:, :] = fill
        out = apply_filter(image, steps, intensity)
        x0, y0, cw, ch = crop
        cases.append(
            {
                "name": name,
                "width": width,
                "height": height,
                "fill": list(fill),
                "steps": steps,
                "intensity": intensity,
                "crop": {"x": x0, "y": y0, "width": cw, "height": ch},
                "expected": flat(out[y0:y0 + ch, x0:x0 + cw]),
            }
        )
    return cases


def hash_vectors() -> Dict[str, Any]:
    inputs = [0, 1, 2, 255, 65536, 123456789, 2147483647, 2147483648, 4294967295]
    hashes = [int(v) for v in lowbias32(np.array(inputs, dtype=np.uint32))]
    cells = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (5, 9, 3), (123, 456, 65535), (1000, 1000, 7), (39, 12, 1)]
    noise = [float(grain_noise(np.array(cx), np.array(cy), seed)) for cx, cy, seed in cells]
    return {
        "inputs": inputs,
        "hashes": hashes,
        "cells": [list(c) for c in cells],
        "noise": noise,
    }


def cube_text(size: int, title: str, domain: bool) -> str:
    lines = [f'TITLE "{title}"', "# synthetic fixture"]
    if domain:
        lines.append("DOMAIN_MIN 0.0 0.0 0.0")
        lines.append("DOMAIN_MAX 1.0 1.0 1.0")
    lines.append(f"LUT_3D_SIZE {size}")
    m = size - 1
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rr, gg, bb = r / m, g / m, b / m
                out_r = min(1.0, max(0.0, 0.9 * rr + 0.1 * gg * gg + 0.03))
                out_g = min(1.0, max(0.0, 0.85 * gg + 0.15 * rr * bb))
                out_b = min(1.0, max(0.0, 0.7 * bb + 0.3 * (1 - rr) * gg + 0.05))
                lines.append(f"{out_r:.6f} {out_g:.6f} {out_b:.6f}")
    return "\n".join(lines) + "\n"


def cube_cases() -> Dict[str, Any]:
    cases = []
    domain_text = (
        "LUT_3D_SIZE 2\nDOMAIN_MIN 0.1 0.1 0.1\nDOMAIN_MAX 0.9 0.9 0.9\n"
        "0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n"
    )
    definitions = [
        ("cube5-only", cube_text(5, "five", False), [], 100),
        ("cube5-half", cube_text(5, "five", False), [], 50),
        ("cube9-with-steps", cube_text(9, "nine", True), [{"op": "tone", "contrast": 15}, {"op": "vignette", "amount": 30}], 100),
        ("cube2-domain", domain_text, [], 100),
    ]
    for name, text, steps, intensity in definitions:
        cube = parse_cube(text)
        cases.append(
            {
                "name": name,
                "cube": text,
                "steps": steps,
                "intensity": intensity,
                "expected": render(steps, intensity, cube),
                "lut_samples": lut_samples([s for s in steps if CORE_OPS[s["op"]].kind == "colour"], cube, intensity),
            }
        )
    bad = [
        {"name": "empty", "cube": "", "error": "missing LUT_3D_SIZE"},
        {"name": "one-d", "cube": "LUT_1D_SIZE 4\n0 0 0\n", "error": "LUT_1D_SIZE"},
        {"name": "wrong-count", "cube": "LUT_3D_SIZE 2\n0 0 0\n1 1 1\n", "error": "expected 8 data rows"},
        {"name": "too-big", "cube": "LUT_3D_SIZE 66\n", "error": "outside 2..65"},
        {"name": "non-finite", "cube": "LUT_3D_SIZE 2\n" + "0 0 0\n" * 7 + "0 0 nan\n", "error": "must be finite"},
        {"name": "bad-row", "cube": "LUT_3D_SIZE 2\n0 0\n", "error": "three numbers"},
        {"name": "data-first", "cube": "0 0 0\nLUT_3D_SIZE 2\n", "error": "before LUT_3D_SIZE"},
        {"name": "bad-domain", "cube": "LUT_3D_SIZE 2\nDOMAIN_MIN 1 1 1\nDOMAIN_MAX 0 0 0\n" + "0 0 0\n" * 8, "error": "DOMAIN_MAX"},
        {"name": "input-range", "cube": "LUT_3D_SIZE 2\nLUT_3D_INPUT_RANGE 0 1\n", "error": "LUT_3D_INPUT_RANGE"},
    ]
    return {"cases": cases, "bad": bad}


def build() -> Dict[str, Dict[str, Any]]:
    image = {"width": WIDTH, "height": HEIGHT, "pixels": image_pixels()}
    return {
        "ops.json": {
            "version": 1,
            "tolerance": 1,
            "image": image,
            "catalogue": [spec.to_dict() for spec in CORE_OPS.values()],
            "cases": op_cases(),
        },
        "filters.json": {"version": 1, "tolerance": 1, "image": image, "cases": filter_cases()},
        "spatial.json": {"version": 1, "cases": spatial_cases()},
        "hash.json": {"version": 1, **hash_vectors()},
        "cube.json": {"version": 1, "tolerance": 1, "image": image, **cube_cases()},
    }


def write(out_dir: Path) -> List[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, payload in build().items():
        (out_dir / name).write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
        written.append(name)
    return written


if __name__ == "__main__":
    print("\n".join(write(GOLDEN_DIR)))
