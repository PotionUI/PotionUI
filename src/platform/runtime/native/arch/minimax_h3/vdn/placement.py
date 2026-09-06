"""What one VDN block costs while it runs, for the DiT placement's reserve.

The two branches never hold their transients at the same time: the windowed softmax's
gathered keys and values are freed before the linear branch's scan banks are allocated,
and the rotated q/k are dropped in between. So the block's peak is the larger of the
two, not their sum.

The linear branch is counted at its PEAK — the bf16 epilogue, not the fp32 scan — the
same worst-case reserve Sol-Attn takes. The epilogue is the larger phase by ~2.5x at
the released 768p shape, and a placement that reserved the scan figure would OOM on the
readout with the card already full. The scan-only figure stays available in the
breakdown for a caller that accounts for block activations separately.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layout import VdnLayout
from .memory import linear_branch_transient_breakdown
from .window import estimate_window_transient_gb

__all__ = ["VdnTransient", "estimate_vdn_transient_gb", "vdn_transient_breakdown"]


@dataclass(frozen=True)
class VdnTransient:
    """One block's three transient phases, in GiB, largest to be reserved."""

    window_gb: float
    scan_gb: float
    readout_gb: float

    @property
    def peak_gb(self) -> float:
        return max(self.window_gb, self.scan_gb, self.readout_gb)


def vdn_transient_breakdown(
    layout: VdnLayout,
    heads: int,
    head_dim: int,
    hidden: int,
    *,
    anchor_frames: str = "both",
    bytes_per_element: int = 2,
) -> VdnTransient:
    """The windowed softmax's peak and the linear branch's two peaks, separately."""
    window = estimate_window_transient_gb(
        layout, heads, head_dim,
        anchor_frames=anchor_frames, bytes_per_element=bytes_per_element,
    )
    branch = linear_branch_transient_breakdown(
        layout.branch_frames, heads, head_dim, layout.tokens_per_frame, hidden
    )
    return VdnTransient(window, branch.scan_gb, branch.readout_gb)


def estimate_vdn_transient_gb(
    layout: VdnLayout,
    heads: int,
    head_dim: int,
    hidden: int,
    *,
    anchor_frames: str = "both",
    bytes_per_element: int = 2,
) -> float:
    """Peak extra VRAM one VDN block needs on top of the dense block's own, in GiB."""
    return vdn_transient_breakdown(
        layout, heads, head_dim, hidden,
        anchor_frames=anchor_frames, bytes_per_element=bytes_per_element,
    ).peak_gb
