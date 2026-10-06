from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

MAX_CUBE_BYTES = 8 * 1024 * 1024
MIN_CUBE_SIZE = 2
MAX_CUBE_SIZE = 65
PREFERRED_CUBE_SIZES = (17, 33, 65)


class CubeError(ValueError):
    pass


@dataclass(frozen=True)
class Cube:
    size: int
    data: np.ndarray
    domain_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    domain_max: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    title: str = ""


def _triple(line_no: int, keyword: str, parts: List[str]) -> Tuple[float, float, float]:
    if len(parts) != 3:
        raise CubeError(f"line {line_no}: {keyword} needs three numbers")
    try:
        values = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise CubeError(f"line {line_no}: {keyword} needs three numbers") from exc
    if not all(math.isfinite(v) for v in values):
        raise CubeError(f"line {line_no}: {keyword} must be finite")
    return (values[0], values[1], values[2])


def parse_cube(text: str) -> Cube:
    size = 0
    title = ""
    domain_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    domain_max: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    rows: List[List[float]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        keyword = parts[0].upper()
        if keyword == "TITLE":
            title = line[len(parts[0]):].strip().strip('"')
        elif keyword == "LUT_3D_SIZE":
            if len(parts) != 2 or not parts[1].isdigit():
                raise CubeError(f"line {line_no}: LUT_3D_SIZE needs one whole number")
            size = int(parts[1])
            if not MIN_CUBE_SIZE <= size <= MAX_CUBE_SIZE:
                raise CubeError(f"line {line_no}: LUT_3D_SIZE {size} is outside {MIN_CUBE_SIZE}..{MAX_CUBE_SIZE}")
        elif keyword == "LUT_1D_SIZE":
            raise CubeError(f"line {line_no}: 1D LUTs are not supported (LUT_1D_SIZE)")
        elif keyword == "LUT_3D_INPUT_RANGE":
            raise CubeError(f"line {line_no}: LUT_3D_INPUT_RANGE is not supported")
        elif keyword == "DOMAIN_MIN":
            domain_min = _triple(line_no, "DOMAIN_MIN", parts[1:])
        elif keyword == "DOMAIN_MAX":
            domain_max = _triple(line_no, "DOMAIN_MAX", parts[1:])
        elif keyword[0].isalpha():
            raise CubeError(f"line {line_no}: unknown keyword '{parts[0]}'")
        else:
            if not size:
                raise CubeError(f"line {line_no}: data before LUT_3D_SIZE")
            if len(parts) != 3:
                raise CubeError(f"line {line_no}: a data row needs three numbers")
            try:
                row = [float(part) for part in parts]
            except ValueError as exc:
                raise CubeError(f"line {line_no}: a data row needs three numbers") from exc
            if not all(math.isfinite(v) for v in row):
                raise CubeError(f"line {line_no}: data values must be finite")
            rows.append(row)
    if not size:
        raise CubeError("missing LUT_3D_SIZE")
    expected = size ** 3
    if len(rows) != expected:
        raise CubeError(f"expected {expected} data rows for LUT_3D_SIZE {size}, found {len(rows)}")
    if any(hi <= lo for lo, hi in zip(domain_min, domain_max)):
        raise CubeError("DOMAIN_MAX must be greater than DOMAIN_MIN on every channel")
    return Cube(
        size=size,
        data=np.asarray(rows, dtype=np.float32),
        domain_min=domain_min,
        domain_max=domain_max,
        title=title,
    )


def read_cube(path) -> Cube:
    from pathlib import Path

    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise CubeError(f"could not read file: {exc}") from exc
    if size > MAX_CUBE_BYTES:
        raise CubeError(f"file is {size} bytes; the limit is {MAX_CUBE_BYTES}")
    try:
        return parse_cube(target.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise CubeError("file is not valid UTF-8 text") from exc
