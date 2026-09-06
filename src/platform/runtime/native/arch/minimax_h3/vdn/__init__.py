"""MiniMax-H3 VDN (Video DeltaNet) hybrid attention.

``attach_vdn_branch`` turns a loaded dense H3 model into a VDN one; from then on a
forward that carries a ``vdn_layout`` runs the hybrid, and one that does not is the
dense model byte for byte.
"""

from .attach import AttachReport, attach_vdn_branch, vdn_attached
from .hybrid_attention import MiniMaxH3VdnAttention
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
from .placement import VdnTransient, estimate_vdn_transient_gb, vdn_transient_breakdown
from .window import (
    ANCHOR_MODES,
    DEFAULT_CHUNK,
    DEFAULT_RADIUS,
    covers_all_frames,
    estimate_window_transient_gb,
    window_bounds,
    windowed_softmax,
)

__all__ = [
    "ANCHOR_MODES",
    "DEFAULT_CHUNK",
    "DEFAULT_RADIUS",
    "AttachReport",
    "LinearBranchTransient",
    "MiniMaxH3BidirectionalLinearAttention",
    "MiniMaxH3FrameAlpha",
    "MiniMaxH3LinearBranch",
    "MiniMaxH3LinearOutputGate",
    "MiniMaxH3ShortConv",
    "MiniMaxH3SoftmaxGate",
    "MiniMaxH3VdnAttention",
    "VdnLayout",
    "VdnTransient",
    "attach_vdn_branch",
    "covers_all_frames",
    "estimate_linear_branch_transient_gb",
    "estimate_vdn_transient_gb",
    "estimate_window_transient_gb",
    "frame_statistics",
    "gather_linear_state",
    "linear_branch_transient_breakdown",
    "run_scans",
    "vdn_attached",
    "vdn_solve",
    "vdn_transient_breakdown",
    "window_bounds",
    "windowed_softmax",
]
