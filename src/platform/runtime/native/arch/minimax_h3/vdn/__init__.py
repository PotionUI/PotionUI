"""MiniMax-H3 VDN (Video DeltaNet) hybrid attention — the linear branch."""

from .layout import VdnLayout
from .linear_branch import (
    MiniMaxH3BidirectionalLinearAttention,
    MiniMaxH3FrameAlpha,
    MiniMaxH3LinearBranch,
    MiniMaxH3LinearOutputGate,
    MiniMaxH3ShortConv,
    MiniMaxH3SoftmaxGate,
    frame_statistics,
    gather_linear_state,
    run_scans,
    vdn_solve,
)
from .memory import (
    LinearBranchTransient,
    estimate_linear_branch_transient_gb,
    linear_branch_transient_breakdown,
)

__all__ = [
    "LinearBranchTransient",
    "MiniMaxH3BidirectionalLinearAttention",
    "MiniMaxH3FrameAlpha",
    "MiniMaxH3LinearBranch",
    "MiniMaxH3LinearOutputGate",
    "MiniMaxH3ShortConv",
    "MiniMaxH3SoftmaxGate",
    "VdnLayout",
    "estimate_linear_branch_transient_gb",
    "frame_statistics",
    "gather_linear_state",
    "linear_branch_transient_breakdown",
    "run_scans",
    "vdn_solve",
]
