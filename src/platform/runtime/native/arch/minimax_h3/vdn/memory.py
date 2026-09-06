"""What one block's VDN linear branch costs while it runs.

The branch's cost has an unusual shape: the scan banks are ``F * H * d^2`` and do not
depend on the spatial resolution at all, so halving the canvas does not help and halving
the clip length does. Placement needs that modelled, not inferred by analogy to dense
attention.

The transient is per block and not cumulative across blocks — every tensor below dies
before the next block's branch runs.
"""

from __future__ import annotations

from dataclasses import dataclass

_BYTES_PER_GB = 1024 ** 3
_FP32_BYTES = 4
_BF16_BYTES = 2

# transitions, injections, the prefix bank, the suffix bank, and the gathered state:
# all [F, H, d, d] fp32, all live at the moment the gather returns.
_SCAN_BANKS = 5

# The readout, the normalised readout and the output gate: three [F*S, H, d] tensors
# live at once at the epilogue, which is the branch's other peak.
_READOUT_COPIES = 3


@dataclass(frozen=True)
class LinearBranchTransient:
    """The branch's two peaks, and the larger of them."""

    scan_gb: float
    readout_gb: float

    @property
    def peak_gb(self) -> float:
        return max(self.scan_gb, self.readout_gb)


def linear_branch_transient_breakdown(
    num_frames: int, heads: int, head_dim: int, tokens_per_frame: int, hidden: int
) -> LinearBranchTransient:
    """Both of the branch's peaks, separately, for one block.

    ``scan_gb`` is the fp32 state working set: the per-frame transitions and injections,
    the two directional state banks, and the gathered state. ``readout_gb`` is the bf16
    epilogue: the readout, its normalised copy and the output gate, plus the gathered
    state once it has been cast down.

    They are separate because they do not overlap — the state banks are dropped before
    the readout is formed — and because only the first is resolution-independent. At the
    released 768p/14.4s shape the epilogue is the larger of the two by roughly 2.5x,
    which the branch's own memory note does not say.
    """
    if min(num_frames, heads, head_dim, tokens_per_frame, hidden) <= 0:
        return LinearBranchTransient(0.0, 0.0)

    state_bytes = num_frames * heads * head_dim * head_dim
    rows = num_frames * tokens_per_frame
    channels = heads * head_dim

    scan = _SCAN_BANKS * state_bytes * _FP32_BYTES
    # Either three readout-shaped copies at the epilogue, or one of them alongside the
    # projected output — whichever of the two moments is larger for this geometry.
    epilogue = max(_READOUT_COPIES * rows * channels, rows * (channels + hidden))
    readout = (epilogue + state_bytes) * _BF16_BYTES
    return LinearBranchTransient(scan / _BYTES_PER_GB, readout / _BYTES_PER_GB)


def estimate_linear_branch_transient_gb(
    num_frames: int, heads: int, head_dim: int, tokens_per_frame: int, hidden: int
) -> float:
    """Peak extra VRAM one block's linear branch needs, in GB.

    Callers hand this to the DiT placement's reserve so a card placed at the
    activation-reserve ceiling does not OOM partway into sampling. It counts only what
    the branch itself allocates; the block's own hidden states and the softmax side's
    q/k/v are the caller's accounting, and the softmax side's are dead by the time the
    branch runs.
    """
    return linear_branch_transient_breakdown(
        num_frames, heads, head_dim, tokens_per_frame, hidden
    ).peak_gb
