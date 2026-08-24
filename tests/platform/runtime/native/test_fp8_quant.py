"""On-the-fly fp8 quantise-at-load tests.

Validate that a bf16 checkpoint quantised by ``ops.fp8_quant`` (a) recovers within
the fp8 grid tolerance, (b) emits exactly the ``weight_scale`` format the runtime
``Fp8ScaledLinear`` consumes, so a round-trip through ``fp8_ops.Linear`` matches an
independent dequant, and (c) leaves norms/embeddings/bias/small layers untouched.
No GPU or real checkpoint needed.
"""

from __future__ import annotations

import json

import pytest

torch = pytest.importorskip("torch")

from src.platform.runtime.native.ops.fp8_quant import (  # noqa: E402
    _E4M3_MAX,
    estimate_fp8_gb,
    quantize_state_dict_to_fp8,
    quantize_tensor_to_fp8_scaled,
    should_quantize_fp8,
)
from vendor.gpl.comfyui.ops import (  # noqa: E402
    _parse_comfy_quant,
    detect_quant_format,
    fp8_ops,
)


def _dequant(q: torch.Tensor, scale: torch.Tensor) -> torch.Tensor:
    return q.to(torch.float32) * scale.to(torch.float32)


def test_tensor_roundtrip_within_fp8_grid():
    torch.manual_seed(0)
    w = torch.randn(256, 512, dtype=torch.bfloat16) * 0.03
    q, scale = quantize_tensor_to_fp8_scaled(w)
    assert q.dtype == torch.float8_e4m3fn
    assert scale.shape == ()
    deq = _dequant(q, scale)
    rel = (deq - w.float()).abs().mean() / w.float().abs().mean()
    # e4m3 has ~3 mantissa bits: grid error, not corruption.
    assert 0.0 < rel < 0.10


def test_all_zero_tensor_stays_zero():
    w = torch.zeros(8, 8)
    q, scale = quantize_tensor_to_fp8_scaled(w)
    assert torch.count_nonzero(_dequant(q, scale)) == 0


def test_scale_maps_amax_near_e4m3_max():
    w = torch.randn(64, 64) * 5.0
    q, scale = quantize_tensor_to_fp8_scaled(w)
    # The largest-magnitude element should sit near (not above) the e4m3 ceiling.
    assert q.to(torch.float32).abs().max() <= _E4M3_MAX
    assert (w.abs().amax() / scale) <= _E4M3_MAX + 1e-3


def test_state_dict_quantises_only_big_2d_weights():
    torch.manual_seed(1)
    sd = {
        "blocks.0.attn.qkv.weight": torch.randn(384, 128) * 0.02,   # quantise
        "blocks.0.attn.qkv.bias": torch.randn(384),                 # keep (1D)
        "norm.weight": torch.randn(128),                            # keep (norm, 1D)
        "img_norm.weight": torch.randn(128, 128) * 0.02,            # keep (2D but 'norm')
        "modulation.lin.weight": torch.randn(256, 128) * 0.02,      # keep ('modulation')
        "txt_embed.weight": torch.randn(1000, 128) * 0.02,          # keep ('embed')
    }
    out, n = quantize_state_dict_to_fp8(sd)
    assert n == 1
    assert out["blocks.0.attn.qkv.weight"].dtype == torch.float8_e4m3fn
    assert "blocks.0.attn.qkv.weight_scale" in out
    # Untouched tensors pass through by identity.
    for k in ("blocks.0.attn.qkv.bias", "norm.weight", "img_norm.weight",
              "modulation.lin.weight", "txt_embed.weight"):
        assert out[k] is sd[k]
    # The emitted sidecar makes the checkpoint detect as scaled-fp8.
    assert detect_quant_format({}, out) == "fp8_scaled"


def test_min_numel_protects_small_linears():
    sd = {
        "big.weight": torch.randn(512, 512) * 0.02,
        "small.weight": torch.randn(8, 8) * 0.02,
    }
    _out, n = quantize_state_dict_to_fp8(sd, min_numel=1024)
    assert n == 1  # only 'big' (262144 elems) crosses the threshold


def test_roundtrip_through_fp8_scaled_linear_matches_dequant():
    """Load the quantised weight into the real runtime Linear and match a manual dequant."""
    torch.manual_seed(2)
    out_f, in_f = 256, 512
    w = torch.randn(out_f, in_f, dtype=torch.bfloat16) * 0.03
    bias = torch.randn(out_f, dtype=torch.bfloat16)

    sd = {"proj.weight": w, "proj.bias": bias}
    qsd, n = quantize_state_dict_to_fp8(sd)
    assert n == 1

    lin = fp8_ops.Linear(in_f, out_f, bias=True)  # Nvfp4Linear / Fp8ScaledLinear
    lin.comfy_cast_weights = True
    missing, unexpected = [], []
    lin._load_from_state_dict(
        {"weight": qsd["proj.weight"], "weight_scale": qsd["proj.weight_scale"], "bias": bias},
        "", {}, True, missing, unexpected, [],
    )
    assert lin.weight_scale is not None and not lin._is_nvfp4

    x = torch.randn(4, in_f, dtype=torch.bfloat16)
    ref_w = _dequant(qsd["proj.weight"], qsd["proj.weight_scale"]).to(torch.bfloat16)
    ref = torch.nn.functional.linear(x, ref_w, bias)
    got = lin(x)
    assert got.dtype == x.dtype
    assert torch.allclose(got.float(), ref.float(), atol=1e-2, rtol=1e-2)


def _q(policy, *, quant_format=None, sd_dtype=torch.bfloat16, bf16_gb=24.0, fp8_gb=12.0, vram_gb=16.0):
    return should_quantize_fp8(
        policy, quant_format=quant_format, sd_dtype=sd_dtype,
        bf16_gb=bf16_gb, fp8_gb=fp8_gb, vram_gb=vram_gb,
    )


def test_policy_off_never_quantizes():
    assert _q("off") is False
    assert _q("off", bf16_gb=100.0, fp8_gb=1.0, vram_gb=8.0) is False


def test_policy_force_quantizes_bf16_but_not_already_quantized():
    assert _q("force") is True
    assert _q("force", quant_format="fp8_scaled") is False   # already fp8
    assert _q("force", sd_dtype=torch.float8_e4m3fn) is False  # non-quantisable dtype


def test_policy_auto_only_in_doesnt_fit_bf16_but_fits_fp8_window():
    # 16GB card, budget 14.4: 24GB bf16 doesn't fit, 12GB fp8 does -> quantise.
    assert _q("auto", bf16_gb=24.0, fp8_gb=12.0, vram_gb=16.0) is True
    # bf16 already fits -> no need.
    assert _q("auto", bf16_gb=10.0, fp8_gb=5.0, vram_gb=16.0) is False
    # doesn't fit even as fp8 -> quantising wouldn't make it resident, skip.
    assert _q("auto", bf16_gb=40.0, fp8_gb=20.0, vram_gb=16.0) is False
    # no budget known -> keep full precision.
    assert _q("auto", bf16_gb=24.0, fp8_gb=12.0, vram_gb=None) is False


def test_unknown_policy_falls_back_to_auto():
    assert _q("garbage", bf16_gb=24.0, fp8_gb=12.0, vram_gb=16.0) is True


def test_fp8_linear_tolerates_fp8_activation_input():
    """Regression: a quantised layer whose activation arrives as fp8 must upcast
    and compute, not crash on an fp8 matmul.

    Some archs derive their activation dtype from a weight's storage dtype — e.g.
    Krea-2's timestep embed does ``temb(..., dtype=tmlp[0].weight.dtype)``. After
    on-the-fly fp8 quantisation that dtype is ``float8_e4m3fn``, so the fp8 Linear
    receives fp8 activations. `mul_cuda`/`F.linear` are undefined for fp8, so the
    dequant path must upcast the input to the compute dtype first (GPU A/B caught
    this on Krea-2)."""
    torch.manual_seed(0)
    out_f, in_f = 64, 64
    w = torch.randn(out_f, in_f, dtype=torch.bfloat16) * 0.02
    qsd, n = quantize_state_dict_to_fp8({"proj.weight": w})
    assert n == 1

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin.comfy_cast_weights = True
    lin._load_from_state_dict(
        {"weight": qsd["proj.weight"], "weight_scale": qsd["proj.weight_scale"]},
        "", {}, True, [], [], [],
    )
    x_fp8 = torch.randn(2, in_f).to(torch.float8_e4m3fn)
    y = lin(x_fp8)
    assert y.dtype == torch.bfloat16
    assert torch.isfinite(y).all()


def test_estimate_fp8_gb_roughly_halves_bf16():
    sd = {
        "a.weight": torch.zeros(4096, 4096, dtype=torch.bfloat16),  # 32MiB bf16 -> 16MiB fp8
        "n.weight": torch.zeros(4096, dtype=torch.bfloat16),        # tiny, unchanged
    }
    est = estimate_fp8_gb(sd)
    bf16_gb = sum(t.numel() * t.element_size() for t in sd.values()) / (1024 ** 3)
    assert est < bf16_gb
    assert est == pytest.approx(bf16_gb * 0.5, rel=0.02)


# ---------------------------------------------------------------------------
# comfy_quant descriptor blob: unknown formats rejected at load, missing or
# corrupt blob = pre-existing behavior. int8_tensorwise and ConvRot live in
# test_int8_convrot.py.
# ---------------------------------------------------------------------------

def _blob(conf: dict) -> torch.Tensor:
    return torch.tensor(list(json.dumps(conf).encode("utf-8")), dtype=torch.uint8)


def test_parse_comfy_quant_none_for_missing_blob():
    assert _parse_comfy_quant(None) is None


def test_parse_comfy_quant_none_for_corrupt_blob():
    garbage = torch.tensor([0xFF, 0xFE, 0x00, 0x01], dtype=torch.uint8)
    assert _parse_comfy_quant(garbage) is None


def test_parse_comfy_quant_decodes_json():
    assert _parse_comfy_quant(_blob({"format": "float8_e4m3fn"})) == {"format": "float8_e4m3fn"}


def test_fp8_layer_with_comfy_quant_blob_loads_unchanged():
    """Regression: qwen-image 2511 fp8 files carry a comfy_quant blob today and
    must keep loading exactly as before."""
    torch.manual_seed(3)
    out_f, in_f = 32, 64
    w = torch.randn(out_f, in_f, dtype=torch.bfloat16) * 0.03
    qsd, n = quantize_state_dict_to_fp8({"proj.weight": w})
    assert n == 1

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin.comfy_cast_weights = True
    lin._load_from_state_dict(
        {
            "weight": qsd["proj.weight"],
            "weight_scale": qsd["proj.weight_scale"],
            "comfy_quant": _blob({"format": "float8_e4m3fn"}),
        },
        "", {}, True, [], [], [],
    )
    assert lin.weight_scale is not None
    x = torch.randn(2, in_f, dtype=torch.bfloat16)
    y = lin(x)
    assert torch.isfinite(y).all()


def test_missing_comfy_quant_blob_loads_as_before():
    torch.manual_seed(4)
    out_f, in_f = 16, 32
    w = torch.randn(out_f, in_f, dtype=torch.bfloat16) * 0.03
    qsd, n = quantize_state_dict_to_fp8({"proj.weight": w})
    assert n == 1

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin.comfy_cast_weights = True
    lin._load_from_state_dict(
        {"weight": qsd["proj.weight"], "weight_scale": qsd["proj.weight_scale"]},
        "", {}, True, [], [], [],
    )
    assert lin.weight_scale is not None


def test_corrupt_comfy_quant_blob_treated_as_no_blob():
    torch.manual_seed(5)
    out_f, in_f = 16, 32
    w = torch.randn(out_f, in_f, dtype=torch.bfloat16) * 0.03
    qsd, n = quantize_state_dict_to_fp8({"proj.weight": w})
    assert n == 1

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin.comfy_cast_weights = True
    garbage = torch.tensor([0xFF, 0xFE, 0x00, 0x01], dtype=torch.uint8)
    lin._load_from_state_dict(
        {
            "weight": qsd["proj.weight"],
            "weight_scale": qsd["proj.weight_scale"],
            "comfy_quant": garbage,
        },
        "", {}, True, [], [], [],
    )
    assert lin.weight_scale is not None










def test_unknown_quant_format_raises_naming_format_and_layer():
    out_f, in_f = 8, 16
    sd = {
        "proj.weight": torch.randn(out_f, in_f, dtype=torch.bfloat16),
        "proj.weight_scale": torch.tensor(1.0),
        "proj.comfy_quant": _blob({"format": "mxfp8"}),
    }
    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin.comfy_cast_weights = True
    with pytest.raises(ValueError, match="mxfp8") as excinfo:
        lin._load_from_state_dict(sd, "proj.", {}, True, [], [], [])
    assert "proj" in str(excinfo.value)
