from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

MAX_CUBE_BYTES = 8 * 1024 * 1024
MIN_CUBE_SIZE = 2
MAX_CUBE_SIZE = 65
PREFERRED_CUBE_SIZES = (17, 33, 65)
MAX_LINE_CHARS = 1024
MAX_TITLE_CHARS = 256
MAX_HEADER_LINES = 64
MIN_SHAPER_SIZE = 2
MAX_SHAPER_SIZE = 65536


class CubeError(ValueError):
    pass


@dataclass(frozen=True)
class Cube:
    size: int
    data: np.ndarray
    domain_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    domain_max: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    title: str = ""
    shaper: Optional[np.ndarray] = None
    shaper_range: Tuple[float, float] = (0.0, 1.0)


def _range(line_no: int, keyword: str, parts: List[str]) -> Tuple[float, float]:
    try:
        values = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise CubeError(f"line {line_no}: {keyword} needs two numbers") from exc
    if len(values) != 2 or not all(math.isfinite(v) for v in values):
        raise CubeError(f"line {line_no}: {keyword} needs two finite numbers")
    if values[1] <= values[0]:
        raise CubeError(f"line {line_no}: {keyword} must rise")
    return (values[0], values[1])


def _size(line_no: int, keyword: str, parts: List[str], low: int, high: int) -> int:
    if len(parts) != 2 or not parts[1].isdigit() or len(parts[1]) > 6:
        raise CubeError(f"line {line_no}: {keyword} needs one whole number")
    value = int(parts[1])
    if not low <= value <= high:
        raise CubeError(f"line {line_no}: {keyword} {value} is outside {low}..{high}")
    return value


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


def parse_cube(text: str, *, allow_shaper: bool = False) -> Cube:
    if len(text) > MAX_CUBE_BYTES:
        raise CubeError(f"the LUT is larger than {MAX_CUBE_BYTES} bytes")
    size = 0
    shaper_size = 0
    shaper_range: Tuple[float, float] = (0.0, 1.0)
    headers = 0
    title = ""
    domain_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    domain_max: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    rows: List[List[float]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if len(raw) > MAX_LINE_CHARS:
            raise CubeError(f"line {line_no}: longer than {MAX_LINE_CHARS} characters")
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        keyword = parts[0].upper()
        if keyword[0].isalpha():
            headers += 1
            if headers > MAX_HEADER_LINES:
                raise CubeError(f"line {line_no}: more than {MAX_HEADER_LINES} header lines")
            if rows:
                raise CubeError(f"line {line_no}: '{parts[0]}' after the data")
        if keyword == "TITLE":
            title = line[len(parts[0]):].strip().strip('"')
            if len(title) > MAX_TITLE_CHARS:
                raise CubeError(f"line {line_no}: TITLE is longer than {MAX_TITLE_CHARS} characters")
        elif keyword == "LUT_3D_SIZE":
            size = _size(line_no, keyword, parts, MIN_CUBE_SIZE, MAX_CUBE_SIZE)
        elif keyword == "LUT_1D_SIZE":
            if not allow_shaper:
                raise CubeError(f"line {line_no}: 1D LUTs are not supported (LUT_1D_SIZE)")
            shaper_size = _size(line_no, keyword, parts, MIN_SHAPER_SIZE, MAX_SHAPER_SIZE)
        elif keyword == "LUT_1D_INPUT_RANGE":
            if not allow_shaper:
                raise CubeError(f"line {line_no}: LUT_1D_INPUT_RANGE is not supported")
            shaper_range = _range(line_no, keyword, parts[1:])
        elif keyword == "LUT_3D_INPUT_RANGE":
            if not allow_shaper:
                raise CubeError(f"line {line_no}: LUT_3D_INPUT_RANGE is not supported")
            low, high = _range(line_no, keyword, parts[1:])
            domain_min, domain_max = (low, low, low), (high, high, high)
        elif keyword == "DOMAIN_MIN":
            domain_min = _triple(line_no, "DOMAIN_MIN", parts[1:])
        elif keyword == "DOMAIN_MAX":
            domain_max = _triple(line_no, "DOMAIN_MAX", parts[1:])
        elif keyword[0].isalpha():
            raise CubeError(f"line {line_no}: unknown keyword '{parts[0]}'")
        else:
            if not size and not shaper_size:
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
        raise CubeError("1D-only LUTs are not supported" if shaper_size else "missing LUT_3D_SIZE")
    expected = shaper_size + size ** 3
    if len(rows) != expected:
        raise CubeError(f"expected {expected} data rows for LUT_3D_SIZE {size}, found {len(rows)}")
    if any(hi <= lo for lo, hi in zip(domain_min, domain_max)):
        raise CubeError("DOMAIN_MAX must be greater than DOMAIN_MIN on every channel")
    table = np.asarray(rows, dtype=np.float32)
    return Cube(
        size=size,
        data=table[shaper_size:],
        domain_min=domain_min,
        domain_max=domain_max,
        title=title,
        shaper=table[:shaper_size] if shaper_size else None,
        shaper_range=shaper_range,
    )


def read_cube(path, *, allow_shaper: bool = False) -> Cube:
    from pathlib import Path

    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise CubeError(f"could not read file: {exc}") from exc
    if size > MAX_CUBE_BYTES:
        raise CubeError(f"file is {size} bytes; the limit is {MAX_CUBE_BYTES}")
    try:
        return parse_cube(target.read_text(encoding="utf-8"), allow_shaper=allow_shaper)
    except UnicodeDecodeError as exc:
        raise CubeError("file is not valid UTF-8 text") from exc
