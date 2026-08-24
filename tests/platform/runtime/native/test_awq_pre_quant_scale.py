"""AWQ ``pre_quant_scale`` activation-smoothing tests.

MiniMax-H3's nvfp4_awq text-encoder repack carries a per-input-channel BF16
``pre_quant_scale`` sidecar on every ``mlp.down_proj``/``self_attn.o_proj``
(50 layers each — verified against ``ai/minimax_h3/te_nvfp4_awq_header.json``).
ModelOpt's AWQ smoothing requires the activation to be multiplied by this scale
BEFORE the quantised matmul; see the provenance note at the top of
``vendor/gpl/comfyui/ops.py`` for the upstream commit this was ported from.
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from vendor.gpl.comfyui.ops import dequantize_nvfp4, fp8_ops  # noqa: E402

from ._nvfp4_ref import F4_MAX as _F4_MAX  # noqa: E402
from ._nvfp4_ref import F8_MAX as _F8_MAX  # noqa: E402
from ._nvfp4_ref import quantize_nvfp4  # noqa: E402


def _nvfp4_sd(out_f: int, in_f: int, w: torch.Tensor, pre_quant_scale: torch.Tensor | None):
    pts = (w.abs().amax() / (_F4_MAX * _F8_MAX)).detach()
    packed, block_sw, _, _ = quantize_nvfp4(w, pts)
    sd = {
        "weight": packed,
        "weight_scale": block_sw,
        "weight_scale_2": pts.clone(),
        "comfy_quant": torch.zeros(5, dtype=torch.uint8),
    }
    if pre_quant_scale is not None:
        sd["pre_quant_scale"] = pre_quant_scale
    return sd, pts


def test_nvfp4_linear_consumes_pre_quant_scale_key():
    torch.manual_seed(0)
    out_f, in_f = 32, 64
    w = torch.randn(out_f, in_f) * 0.03
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75
    sd, _ = _nvfp4_sd(out_f, in_f, w, pqs)

    lin = fp8_ops.Linear(in_f, out_f, bias=False)  # Nvfp4Linear
    missing, unexpected = [], []
    lin._load_from_state_dict(dict(sd), "", {}, True, missing, unexpected, [])
    assert missing == [] and unexpected == []
    assert lin.pre_quant_scale is not None
    assert torch.equal(lin.pre_quant_scale, pqs)


def test_nvfp4_linear_applies_pre_quant_scale_to_the_activation():
    """The forward output must equal ``F.linear(input * pre_quant_scale, dequant_weight)``
    — the scale multiplies the ACTIVATION, not the weight."""
    torch.manual_seed(1)
    out_f, in_f = 32, 64
    w = torch.randn(out_f, in_f) * 0.03
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75
    sd, pts = _nvfp4_sd(out_f, in_f, w, pqs)
    packed, block_sw = sd["weight"], sd["weight_scale"]

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin._load_from_state_dict(dict(sd), "", {}, True, [], [], [])

    x = torch.randn(3, in_f, dtype=torch.bfloat16)
    dequant_w = dequantize_nvfp4(packed, block_sw, pts, out_f, in_f).to(torch.bfloat16)
    expected = torch.nn.functional.linear(x * pqs.to(x.dtype), dequant_w, None)
    got = lin(x)
    assert torch.allclose(got.float(), expected.float(), atol=1e-3, rtol=1e-3)


def test_nvfp4_linear_pre_quant_scale_is_load_bearing():
    """Bite-check: an UNSCALED reference (weight applied to the raw activation,
    skipping the AWQ smoothing multiply) must differ from the real output —
    otherwise this test structurally cannot catch a regression that silently
    drops ``pre_quant_scale``."""
    torch.manual_seed(2)
    out_f, in_f = 32, 64
    w = torch.randn(out_f, in_f) * 0.03
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75
    sd, pts = _nvfp4_sd(out_f, in_f, w, pqs)
    packed, block_sw = sd["weight"], sd["weight_scale"]

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin._load_from_state_dict(dict(sd), "", {}, True, [], [], [])

    x = torch.randn(3, in_f, dtype=torch.bfloat16)
    dequant_w = dequantize_nvfp4(packed, block_sw, pts, out_f, in_f).to(torch.bfloat16)
    unscaled = torch.nn.functional.linear(x, dequant_w, None)
    got = lin(x)
    assert not torch.allclose(got.float(), unscaled.float(), atol=1e-3, rtol=1e-3)


def test_nvfp4_linear_without_pre_quant_scale_key_is_unaffected():
    """A checkpoint with no AWQ sidecar (every other nvfp4 layer in the wild)
    must load and run exactly as before — no ``pre_quant_scale`` means no
    activation scaling."""
    torch.manual_seed(3)
    out_f, in_f = 16, 32
    w = torch.randn(out_f, in_f) * 0.03
    sd, pts = _nvfp4_sd(out_f, in_f, w, None)
    packed, block_sw = sd["weight"], sd["weight_scale"]

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin._load_from_state_dict(dict(sd), "", {}, True, [], [], [])
    assert lin.pre_quant_scale is None

    x = torch.randn(3, in_f)
    dequant_w = dequantize_nvfp4(packed, block_sw, pts, out_f, in_f)
    expected = torch.nn.functional.linear(x, dequant_w, None)
    assert torch.allclose(lin(x), expected)


def test_fp8_scaled_linear_also_applies_pre_quant_scale():
    """The base Fp8ScaledLinear path (fp8-scaled, non-nvfp4) applies the same
    mechanism — upstream loads ``pre_quant_scale`` generically per-layer, not
    only for the nvfp4 format."""
    torch.manual_seed(4)
    out_f, in_f = 16, 32
    w = torch.randn(out_f, in_f, dtype=torch.bfloat16) * 0.03
    q = w.to(torch.float8_e4m3fn)
    scale = torch.tensor(1.0)
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75

    lin = fp8_ops.Linear(in_f, out_f, bias=False)  # Nvfp4Linear, falls through to Fp8ScaledLinear
    lin.comfy_cast_weights = True
    lin._load_from_state_dict(
        {"weight": q, "weight_scale": scale, "pre_quant_scale": pqs}, "", {}, True, [], [], [],
    )
    assert not lin._is_nvfp4
    assert lin.pre_quant_scale is not None

    x = torch.randn(2, in_f, dtype=torch.bfloat16)
    expected = torch.nn.functional.linear(x * pqs.to(x.dtype), q.to(torch.bfloat16) * scale.to(torch.bfloat16), None)
    got = lin(x)
    assert torch.allclose(got.float(), expected.float(), atol=1e-2, rtol=1e-2)
