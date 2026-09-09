"""Why a LoRA delta is not baked into float8 storage.

Folding the delta into the weight and requantising would make a repeat
generation cost exactly the no-LoRA time, which is why it was built and
measured. It is not shipped because fp8 e4m3 carries three mantissa bits: a
delta smaller than about half a quantisation step of the weight it is added to
rounds straight back off, and a LoRA delta at realistic strengths IS that
small.

Measured on a 3072x3072 layer whose dequantised weight is ~N(0, 0.02), with a
rank-16 delta at three fractions of the weight's Frobenius norm. ``retention``
is how far the storage moved relative to the exact delta; ``projection`` is how
much of the delta actually landed, ``<realised, exact> / <exact, exact>`` --
the honest number, because norm alone counts rounding noise as if it were
signal.

    per-tensor scale (max|W| -> 448)      no scale (values stored directly)
    delta/|W|  retention  projection     delta/|W|  retention  projection
        0.5%      50.3%       19.0%          0.5%       0.0%        0.0%
        1.0%      69.3%       36.3%          1.0%       4.9%        0.1%
        3.0%      99.3%       79.6%          3.0%      98.6%       62.6%

A user asking for strength 1.0 and getting between 0.1% and 36% of the adapter
is not a performance optimisation. The runtime path keeps every delta exact;
see ``linear_with_lora_deltas`` in ``vendor/gpl/comfyui/ops.py`` for how it was
made cheap instead.

The test below reproduces the middle row. It is skipped: it exists to keep the
measurement runnable and honest if anyone proposes the bake again, not to gate
the build on a stochastic quantisation figure.
"""

from __future__ import annotations

import pytest
import torch

from vendor.gpl.comfyui.ops import pick_operations

OUT, IN, RANK = 3072, 3072, 16


def _requantise(weight: torch.Tensor, scale, dtype: torch.dtype) -> torch.Tensor:
    out = weight.to(torch.float32)
    if scale is not None:
        out = out / scale.to(torch.float32)
    limit = float(torch.finfo(dtype).max)
    return out.clamp_(-limit, limit).to(dtype)


@pytest.mark.skip(reason=(
    "documentation, not a gate: baking a rank-16 delta at 1% of |W| into fp8 "
    "storage lands 36% of it with a per-tensor scale and 0.1% without -- see "
    "this module's docstring for the full table and why the bake was dropped"
))
def test_a_one_percent_delta_mostly_rounds_off_when_baked_into_fp8():
    torch.manual_seed(0)
    dense = torch.randn(OUT, IN) * 0.02
    scale = torch.tensor(float(dense.abs().max()) / 448.0)
    lin = pick_operations(torch.float8_e4m3fn, torch.bfloat16).Linear(IN, OUT, bias=False)
    lin.weight_scale = scale
    lin.weight.data = (dense / scale).to(torch.float8_e4m3fn)
    base = (lin.weight.data.float() * scale).detach()

    torch.manual_seed(1)
    up, down = torch.randn(OUT, RANK), torch.randn(RANK, IN)
    exact = up @ down
    exact = (exact * (float(base.norm()) * 0.01 / float(exact.norm()))).detach()

    baked = _requantise(base + exact, scale, torch.float8_e4m3fn).float() * scale
    realised = (baked - base).detach()
    projection = float((realised * exact).sum() / (exact * exact).sum())

    assert projection < 0.5, "the bake would have to land most of the delta to be shippable"
