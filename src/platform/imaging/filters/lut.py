from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from src.platform.imaging.filters import colour as colour_ops
from src.platform.imaging.filters.cube import Cube
from src.platform.imaging.filters.ops import CORE_OPS, OpSpec, resolve_params, split_steps

BASE_SIZE = 33
CLIP_MARGIN = 0.1
CHUNK = 1 << 18


class FilterOpUnavailable(Exception):
    def __init__(self, op_id: str):
        super().__init__(f"op '{op_id}' has no implementation in this process")
        self.op_id = op_id


@dataclass(frozen=True)
class Lut:
    size: int
    data: np.ndarray


def lattice(size: int) -> np.ndarray:
    index = np.arange(size ** 3)
    r = index % size
    g = (index // size) % size
    b = index // (size * size)
    return np.stack([r, g, b], axis=1).astype(np.float64) / (size - 1)


def identity_lut(size: int = BASE_SIZE) -> Lut:
    return Lut(size, lattice(size).astype(np.float32))


def _tetra(data: np.ndarray, n: int, pos: np.ndarray) -> np.ndarray:
    nn = n * n
    x = pos[:, 0]
    y = pos[:, 1]
    z = pos[:, 2]
    xi = np.minimum(x.astype(np.int64), n - 2)
    yi = np.minimum(y.astype(np.int64), n - 2)
    zi = np.minimum(z.astype(np.int64), n - 2)
    fr = (x - xi)[:, None]
    fg = (y - yi)[:, None]
    fb = (z - zi)[:, None]
    o = zi * nn + yi * n + xi
    corners = np.stack(
        [data[o], data[o + 1], data[o + n], data[o + nn], data[o + 1 + n], data[o + 1 + nn], data[o + n + nn], data[o + 1 + n + nn]]
    )
    out = np.empty_like(corners[0])

    rg = fr >= fg
    gb = fg >= fb
    rb = fr >= fb
    bg = fb >= fg
    br = fb >= fr
    cases = (
        (rg & gb, _case_a),
        (rg & ~gb & rb, _case_b),
        (rg & ~gb & ~rb, _case_c),
        (~rg & bg, _case_d),
        (~rg & ~bg & br, _case_e),
        (~rg & ~bg & ~br, _case_f),
    )
    for mask, formula in cases:
        flat = mask[:, 0]
        if flat.any():
            out[flat] = formula(corners[:, flat], fr[flat], fg[flat], fb[flat])
    return out


def _case_a(c, fr, fg, fb):
    return c[0] + fr * (c[1] - c[0]) + fg * (c[4] - c[1]) + fb * (c[7] - c[4])


def _case_b(c, fr, fg, fb):
    return c[0] + fr * (c[1] - c[0]) + fb * (c[5] - c[1]) + fg * (c[7] - c[5])


def _case_c(c, fr, fg, fb):
    return c[0] + fb * (c[3] - c[0]) + fr * (c[5] - c[3]) + fg * (c[7] - c[5])


def _case_d(c, fr, fg, fb):
    return c[0] + fb * (c[3] - c[0]) + fg * (c[6] - c[3]) + fr * (c[7] - c[6])


def _case_e(c, fr, fg, fb):
    return c[0] + fg * (c[2] - c[0]) + fb * (c[6] - c[2]) + fr * (c[7] - c[6])


def _case_f(c, fr, fg, fb):
    return c[0] + fg * (c[2] - c[0]) + fr * (c[4] - c[2]) + fb * (c[7] - c[4])


def tetra_sample(data: np.ndarray, n: int, pos: np.ndarray) -> np.ndarray:
    out = np.empty((pos.shape[0], 3), dtype=np.float64)
    for start in range(0, pos.shape[0], CHUNK):
        out[start:start + CHUNK] = _tetra(data, n, pos[start:start + CHUNK])
    return out


def sample_cube(cube: Cube, c: np.ndarray) -> np.ndarray:
    dmin = np.asarray(cube.domain_min, dtype=np.float64)
    dmax = np.asarray(cube.domain_max, dtype=np.float64)
    u = np.clip((c - dmin) / (dmax - dmin), 0.0, 1.0)
    return tetra_sample(cube.data.astype(np.float64), cube.size, u * (cube.size - 1))


def _colour_map(
    step: Mapping[str, Any],
    ops: Mapping[str, OpSpec],
    impls: Optional[Mapping[str, Any]],
) -> Callable[[np.ndarray], np.ndarray]:
    op_id = step["op"]
    spec = ops[op_id]
    params = resolve_params(spec, step)
    builder = colour_ops.COLOUR_OPS.get(op_id) if op_id in CORE_OPS else None
    if builder is not None:
        return builder(params)
    impl = (impls or {}).get(op_id)
    if impl is None or not hasattr(impl, "map"):
        raise FilterOpUnavailable(op_id)

    def run(c: np.ndarray) -> np.ndarray:
        return np.asarray(impl.map(c.astype(np.float32), params), dtype=np.float64)

    return run


def _run_nodes(
    steps: Sequence[Mapping[str, Any]],
    cube: Optional[Cube],
    ops: Optional[Mapping[str, OpSpec]],
    impls: Optional[Mapping[str, Any]],
) -> "tuple[np.ndarray, np.ndarray, np.ndarray]":
    known = ops if ops is not None else CORE_OPS
    colour_steps, _ = split_steps(steps, known)
    size = max(BASE_SIZE, cube.size) if cube is not None else BASE_SIZE
    ident = lattice(size)
    c = ident.copy()
    if cube is not None:
        c = sample_cube(cube, c)
    clipped = np.zeros(c.shape[0], dtype=bool)
    for step in colour_steps:
        raw = _colour_map(step, known, impls)(c)
        clipped |= ((raw < -CLIP_MARGIN) | (raw > 1 + CLIP_MARGIN)).any(axis=1)
        c = np.clip(raw, 0.0, 1.0)
    return ident, c, clipped


def compile_lut(
    steps: Sequence[Mapping[str, Any]],
    cube: Optional[Cube] = None,
    intensity: float = 100,
    ops: Optional[Mapping[str, OpSpec]] = None,
    impls: Optional[Mapping[str, Any]] = None,
) -> Lut:
    ident, c, _ = _run_nodes(steps, cube, ops, impls)
    k = intensity / 100
    size = round(ident.shape[0] ** (1 / 3))
    return Lut(size, (ident + k * (c - ident)).astype(np.float32))


def clipped_fraction(
    steps: Sequence[Mapping[str, Any]],
    cube: Optional[Cube] = None,
    ops: Optional[Mapping[str, OpSpec]] = None,
    impls: Optional[Mapping[str, Any]] = None,
) -> float:
    _, _, clipped = _run_nodes(steps, cube, ops, impls)
    return float(clipped.mean())


def round_half_up(values: np.ndarray) -> np.ndarray:
    return np.floor(values + 0.5)


def apply_lut(rgb: np.ndarray, lut: Lut) -> np.ndarray:
    shape = rgb.shape
    flat = rgb.reshape(-1, 3)
    n = lut.size
    data = lut.data.astype(np.float64)
    out = np.empty(flat.shape, dtype=np.uint8)
    for start in range(0, flat.shape[0], CHUNK):
        part = flat[start:start + CHUNK].astype(np.float64)
        mapped = _tetra(data, n, part / 255 * (n - 1))
        out[start:start + CHUNK] = round_half_up(np.clip(mapped, 0.0, 1.0) * 255).astype(np.uint8)
    return out.reshape(shape)
