"""Normalized Attention Guidance (NAG) — published technique, arXiv 2505.21179.

NAG injects negative-prompt influence *inside* text cross-attention so a single
forward pass per denoising step carries negative guidance, instead of CFG's
second full forward (:class:`~src.platform.runtime.native.sampling.cfg.TrueCFG`). Inside a
cross-attention module: run the normal positive attention (queries against the
positive text K/V) to get ``pos``; project the *negative* text context through
the SAME ``to_k``/``to_v`` weights (and the same k-norm, if any) and run a
second attention of the same queries against that negative K/V to get ``neg``;
then blend on the attention output (post-attention, pre-out-projection):

    g = pos*scale - neg*(scale-1)                # extrapolate away from negative
    r = ||g||_1 / ||pos||_1                       # L1 norm over the feature dim
    g = where(r > tau, g * (tau*||pos||_1 / (||g||_1 + eps)), g)   # norm clamp
    out = g*alpha + pos*(1-alpha)                 # blend back toward positive

``scale <= 1.0`` is "off" (``g == pos``, ``out == pos``); callers should skip
the extra negative attention pass entirely in that case rather than call
:func:`apply_nag` with a no-op scale, to keep the disabled path byte-identical
to pre-NAG behaviour and free of the extra compute.
"""

from __future__ import annotations

import torch

Tensor = torch.Tensor

DEFAULT_TAU = 3.5
DEFAULT_ALPHA = 0.5


def apply_nag(
    pos: Tensor,
    neg: Tensor,
    scale: float,
    tau: float = DEFAULT_TAU,
    alpha: float = DEFAULT_ALPHA,
) -> Tensor:
    """Blend a positive and negative cross-attention output via NAG.

    ``pos``/``neg`` are the (post-attention, pre-out-projection) tensors from
    attending the same queries against the positive and negative text K/V
    respectively — same shape, any leading dims, blend computed over the last
    (feature) dim. Math is done in ``pos``'s dtype, matching the surrounding
    model code (no forced fp32 upcast).
    """
    guided = pos * scale - neg * (scale - 1.0)

    pos_norm = pos.norm(p=1, dim=-1, keepdim=True)
    guided_norm = guided.norm(p=1, dim=-1, keepdim=True)

    ratio = torch.nan_to_num(guided_norm / (pos_norm + 1e-7), nan=10.0, posinf=10.0, neginf=10.0)
    clamped = guided * (tau * pos_norm / (guided_norm + 1e-7))
    guided = torch.where(ratio > tau, clamped, guided)

    return guided * alpha + pos * (1.0 - alpha)
