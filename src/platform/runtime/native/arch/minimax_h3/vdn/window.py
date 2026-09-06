"""The VDN windowed softmax branch, as gathered dense attention calls.

# Derived from: OpenVDN/vdn-minimax-h3 (Apache-2.0)
#   src/models/softmax_attention/window.py (window geometry, mask semantics, anchors)
#   src/models/softmax_attention/decomposed.py (query-group decomposition)

The mask this implements is

    keep(q, kv) = not (q_is_video and kv_is_video) or inside_window

with ``anchor_frames="both"`` additionally making frames 0 and F-1 dense as
columns (every video query sees all of both) and dense as rows (their own
queries see the whole sequence). Everything that is not target video -- text,
condition audio, keyframe-condition rows, target audio -- is dense in both
directions.

It is never expressed as a mask tensor. A dense ``attn_mask=`` at H3 scale is a
56 x 105k x 105k boolean, which is slower than plain dense attention and does not
fit -- so the window has to be a union of dense rectangles instead. Query rows
whose kept key set is identical are grouped, each group's keys and values are
gathered once, and one dense call runs over the gathered set. Softmax over the
gathered set IS the masked softmax, so this is exact, not an approximation.

Every dense call goes through the engine's own backend dispatch, so the window
runs on whatever kernel (sage / flash / sdpa) the base model is running on.
"""

from __future__ import annotations

import math

import torch
from torch import Tensor, nn

from ....attention import attention as _dispatch_attention
from .layout import VdnLayout

__all__ = [
    "ANCHOR_MODES",
    "DEFAULT_CHUNK",
    "DEFAULT_RADIUS",
    "SOFTMAX_GATE_INIT",
    "build_softmax_gate",
    "clear_window_plan_cache",
    "covers_all_frames",
    "estimate_window_transient_gb",
    "softmax_gate",
    "window_bounds",
    "windowed_softmax",
]

DEFAULT_CHUNK = 5
DEFAULT_RADIUS = 1

# "both" is the only mode the released checkpoint was trained under, and the only
# one under which the softmax window and the linear branch are an exact partition
# (VdnLayout.skip_ends is its partner on the branch side). The others exist to
# isolate one half of the anchor rule in a test.
ANCHOR_MODES = ("none", "columns", "rows", "both")

SOFTMAX_GATE_INIT = 0.99

_PLAN_CACHE: dict[tuple, "_WindowPlan"] = {}
MAX_CACHED_PLANS = 4


def window_bounds(
    num_frames: int,
    chunk: int = DEFAULT_CHUNK,
    radius: int = DEFAULT_RADIUS,
) -> list[tuple[int, int]]:
    """Per-frame inclusive softmax-window bounds ``[lo, hi]``, unclamped.

    ``chunk <= 0`` is FRAME mode: the centered window ``|t_q - t_k| <= radius``.

    ``chunk == K`` is CHUNK-ALIGNED mode: frame ``t`` belongs to chunk ``t // K``
    and sees whole chunks ``[c - radius, c + radius]``. The window is a property
    of the chunk, not of the frame, so frames 5 and 9 (both chunk 1 at K=5) get
    the identical window. Alignment is the point, not width: the video VAE codes
    every K latent frames as one unit, and a frame seeing part of a neighbouring
    chunk sees a fragment of something never coded as separable. A centered frame
    window cannot express that -- whatever the radius, some frame straddles a
    boundary.

    Bounds are returned unclamped; clamping to ``[0, num_frames - 1]`` happens at
    use, and the last chunk being short when K does not divide the frame count is
    expected rather than an error. The result is what ``VdnLayout.window_bounds``
    carries, in the full frame numbering -- the branch's rebase under
    ``skip_ends`` is ``VdnLayout.branch_bounds``, and the softmax side must never
    see the rebased list.
    """
    if radius < 0:
        raise ValueError(f"radius={radius} must be non-negative")
    if chunk <= 0:
        return [(t - radius, t + radius) for t in range(num_frames)]
    return [
        (((t // chunk) - radius) * chunk, ((t // chunk) + radius + 1) * chunk - 1)
        for t in range(num_frames)
    ]


def _anchor_frames(num_frames: int, anchor_frames: str) -> tuple[frozenset[int], frozenset[int]]:
    """(dense rows, dense columns) among the anchor frames, for this mode."""
    if anchor_frames not in ANCHOR_MODES:
        raise ValueError(f"anchor_frames={anchor_frames!r}; expected one of {ANCHOR_MODES}")
    anchors = frozenset({0, num_frames - 1})
    rows = anchors if anchor_frames in ("rows", "both") else frozenset()
    columns = anchors if anchor_frames in ("columns", "both") else frozenset()
    return rows, columns


def _key_frames(bounds, frame: int, num_frames: int, columns: frozenset[int]) -> frozenset[int]:
    """The video frames one query frame keeps, clamped, anchors folded in.

    A set, not a list: an anchor frame that already falls inside ``[lo, hi]``
    must not be gathered twice, because a duplicated key doubles its share of
    the softmax mass.
    """
    low, high = bounds[frame]
    return frozenset(range(max(low, 0), min(high, num_frames - 1) + 1)) | columns


def covers_all_frames(num_frames: int, bounds, anchor_frames: str = "both") -> bool:
    """Does every video query already keep every video frame?

    When it does, the window IS dense attention: the caller runs its stock dense
    dispatch (matching the kernel the rest of the model runs on, which a bare
    SDPA call would not) and disables the linear branch, which would otherwise
    double-count a region nothing excluded it from.
    """
    _, columns = _anchor_frames(num_frames, anchor_frames)
    return all(
        len(_key_frames(bounds, frame, num_frames, columns)) == num_frames
        for frame in range(num_frames)
    )


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort half-open ranges and fuse the ones that touch or overlap."""
    merged: list[tuple[int, int]] = []
    for start, stop in sorted(ranges):
        if merged and merged[-1][1] >= start:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
        else:
            merged.append((start, stop))
    return merged


def _rows(ranges: list[tuple[int, int]]) -> int:
    return sum(stop - start for start, stop in ranges)


def _global_ranges(layout: VdnLayout) -> list[tuple[int, int]]:
    """Half-open row ranges of everything that is not target video."""
    candidates = ((0, layout.video_start), (layout.video_end, layout.seq_len))
    return [(start, stop) for start, stop in candidates if start < stop]


def _plan_ranges(layout: VdnLayout, anchor_frames: str):
    """Row ranges per attention call, as plain Python -- no allocation.

    Returns the dense leg's query ranges plus one ``(query, key)`` pair per
    window group. The dense leg carries the global rows and the anchor rows; its
    keys are the whole sequence, so it names no key ranges and gathers nothing.
    """
    num_frames, tokens = layout.num_frames, layout.tokens_per_frame
    bounds = layout.window_bounds
    rows, columns = _anchor_frames(num_frames, anchor_frames)
    globals_ = _global_ranges(layout)

    def frame_rows(frame: int) -> tuple[int, int]:
        return (layout.video_start + frame * tokens, layout.video_start + (frame + 1) * tokens)

    dense = _merge(globals_ + [frame_rows(frame) for frame in sorted(rows)])

    # Chunk-aligned bounds give every frame in a chunk the same key set, so the
    # group count tracks the chunk count rather than the frame count. Grouping on
    # the CLAMPED set also fuses chunks that clamping made identical at the ends.
    grouped: dict[frozenset[int], list[int]] = {}
    for frame in range(num_frames):
        if frame in rows:
            continue
        grouped.setdefault(_key_frames(bounds, frame, num_frames, columns), []).append(frame)

    groups = [
        (
            _merge([frame_rows(frame) for frame in query_frames]),
            _merge(globals_ + [frame_rows(frame) for frame in sorted(key_frames)]),
        )
        for key_frames, query_frames in grouped.items()
    ]

    covered = _rows(dense) + sum(_rows(query) for query, _ in groups)
    if covered != layout.seq_len:
        raise ValueError(f"window decomposition covers {covered} of {layout.seq_len} rows")
    return dense, groups


class _WindowPlan:
    __slots__ = ("dense_query", "groups")

    def __init__(self, layout: VdnLayout, anchor_frames: str, device) -> None:
        dense, groups = _plan_ranges(layout, anchor_frames)

        def index(ranges) -> Tensor:
            if not ranges:
                return torch.empty(0, dtype=torch.long, device=device)
            return torch.cat([torch.arange(a, b, device=device) for a, b in ranges])

        self.dense_query = index(dense)
        self.groups = [(index(query), index(key)) for query, key in groups]


def _plan(layout: VdnLayout, anchor_frames: str, device) -> _WindowPlan:
    key = (layout, anchor_frames, str(device))
    plan = _PLAN_CACHE.get(key)
    if plan is None:
        plan = _PLAN_CACHE[key] = _WindowPlan(layout, anchor_frames, device)
        while len(_PLAN_CACHE) > MAX_CACHED_PLANS:
            _PLAN_CACHE.pop(next(iter(_PLAN_CACHE)))
    return plan


def clear_window_plan_cache() -> None:
    _PLAN_CACHE.clear()


def _dense_attention(query: Tensor, key: Tensor, value: Tensor, backend: str | None) -> Tensor:
    """``[rows, H, d]`` operands through the engine's own backend dispatch."""
    attended = _dispatch_attention(
        query.permute(1, 0, 2).unsqueeze(0),
        key.permute(1, 0, 2).unsqueeze(0),
        value.permute(1, 0, 2).unsqueeze(0),
        heads=query.shape[1],
        mask=None,
        backend=backend,
    )
    return attended.squeeze(0).permute(1, 0, 2)


def windowed_softmax(
    query: Tensor,
    key: Tensor,
    value: Tensor,
    layout: VdnLayout,
    *,
    heads: int,
    anchor_frames: str = "both",
    backend: str | None = None,
) -> Tensor | None:
    """Exact windowed attention over a packed sequence. ``[T, H, d]`` in and out.

    ``query``/``key``/``value`` are already QK-normed and RoPE'd, laid out row-
    major over the packed sequence; the window comes from ``layout.window_bounds``
    in the full frame numbering. Returns ``None`` when the window covers every
    frame -- see :func:`covers_all_frames`; the caller then runs its stock dense
    path and turns the linear branch off.
    """
    if query.ndim != 3:
        raise ValueError(f"expected [T, H, d] operands, got {tuple(query.shape)}")
    if query.shape[1] != heads:
        raise ValueError(f"heads={heads} disagrees with query.shape[1]={query.shape[1]}")
    if query.shape[0] != layout.seq_len:
        raise ValueError(f"{query.shape[0]} rows but the layout describes {layout.seq_len}")
    if covers_all_frames(layout.num_frames, layout.window_bounds, anchor_frames):
        return None

    plan = _plan(layout, anchor_frames, query.device)
    out = torch.empty_like(query)
    if plan.dense_query.numel():
        # Globals and anchor rows keep the whole sequence, so this leg reads key
        # and value in place instead of gathering a second copy of them.
        out[plan.dense_query] = _dense_attention(
            query.index_select(0, plan.dense_query), key, value, backend
        )
    for query_index, key_index in plan.groups:
        gathered_key = key.index_select(0, key_index)
        gathered_value = value.index_select(0, key_index)
        out[query_index] = _dense_attention(
            query.index_select(0, query_index), gathered_key, gathered_value, backend
        )
        # Freed before the next group gathers, so the peak holds one group's
        # keys and values rather than the whole decomposition's.
        del gathered_key, gathered_value
    return out


def estimate_window_transient_gb(
    layout: VdnLayout,
    heads: int,
    head_dim: int,
    *,
    anchor_frames: str = "both",
    bytes_per_element: int = 2,
) -> float:
    """Peak transient of one :func:`windowed_softmax` call, in GB.

    The decomposition allocates per leg and frees before the next, so the peak is
    the widest single leg, not their sum:

        window group   (2*K_max + 2*Q_max) * H * d * bytes
        dense leg      (2*Q_dense) * H * d * bytes

    ``K_max`` is the widest gathered key set -- ``(2*radius + 1) * chunk`` frames
    plus the two anchor frames, times the tokens per frame, plus every global
    row. The factors of two count key and value on one side, the gathered query
    and its output on the other. Excludes whatever workspace the chosen attention
    kernel allocates internally.
    """
    dense, groups = _plan_ranges(layout, anchor_frames)
    per_row = heads * head_dim * bytes_per_element
    peak = 2 * _rows(dense) * per_row
    for query, key in groups:
        peak = max(peak, (2 * _rows(key) + 2 * _rows(query)) * per_row)
    return peak / 1024 ** 3


def build_softmax_gate(
    hidden_size: int,
    heads: int,
    *,
    init_value: float = SOFTMAX_GATE_INIT,
    dtype: torch.dtype | None = None,
    device: torch.device | str | None = None,
) -> nn.Linear:
    """The per-(token, head) gate on the windowed softmax output.

    Zero weight and a ``logit(init_value)`` bias, so every token starts at
    ``init_value``: the windowed softmax renormalises to 1 no matter how little
    mass it saw, and this gate scales it back toward the share it actually
    captured. At 0.99 the softmax branch is still the teacher on step 0.
    """
    if not 0.0 < init_value < 1.0:
        raise ValueError(f"init_value={init_value} must lie in (0, 1)")
    gate = nn.Linear(hidden_size, heads, bias=True, dtype=dtype, device=device)
    nn.init.zeros_(gate.weight)
    nn.init.constant_(gate.bias, math.log(init_value / (1.0 - init_value)))
    return gate


def softmax_gate(x: Tensor, gate_linear: nn.Linear) -> Tensor:
    """``[T, hidden]`` hidden states -> ``[T, H, 1]``, broadcasting over channels."""
    return torch.sigmoid(gate_linear(x)).unsqueeze(-1)
