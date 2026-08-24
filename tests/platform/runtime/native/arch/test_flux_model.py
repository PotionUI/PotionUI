"""Tests for the vendored Flux1 / Flux2 (Klein) architecture module.

Coverage:
  (a) tiny-config construction + CPU fp32 forward smoke (correct output shape);
  (b) meta-device key-set parity vs the REAL Klein header key list (fixture);
  (c) ``from_config`` consumes the exact Klein detect dict;
  (d) ``post_load`` smoke (documented no-op);
  plus Flux1-vs-Flux2 branching, fp8 scale-key popping, and param validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.arch.flux.config import FluxParams
from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.base import NativeArchModule, load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.errors import NativeEngineLoadIntegrityError
from vendor.gpl.comfyui.ops import pick_operations

_FIXTURES = Path(__file__).parent / "fixtures"

# The exact config emitted by detect_unet_config for flux2Klein_9b.safetensors.
KLEIN_CONFIG = {
    "image_model": "flux2", "hidden_size": 4096, "num_heads": 32, "depth": 8,
    "depth_single_blocks": 24, "in_channels": 128, "out_channels": 128,
    "context_in_dim": 12288, "axes_dim": [32, 32, 32, 32], "mlp_ratio": 3.0,
    "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False,
}

# Tiny flux2: 4 rope axes (so txt_ids_dims=[3] is valid), head_dim==sum(axes)==32.
TINY_FLUX2 = {
    "image_model": "flux2", "hidden_size": 64, "num_heads": 2, "depth": 1,
    "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
    "context_in_dim": 32, "axes_dim": [8, 8, 8, 8], "mlp_ratio": 3.0,
    "theta": 2000, "patch_size": 1, "qkv_bias": False, "guidance_embed": False,
}
# Tiny flux1: 3 rope axes, biases, patch_size 2, pooled vector_in.
TINY_FLUX1 = {
    "image_model": "flux", "hidden_size": 64, "num_heads": 2, "depth": 1,
    "depth_single_blocks": 1, "in_channels": 16, "out_channels": 16,
    "context_in_dim": 32, "axes_dim": [8, 12, 12], "mlp_ratio": 4.0,
    "theta": 10000, "patch_size": 2, "qkv_bias": True, "guidance_embed": True,
}


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _randomised_state_dict(module) -> dict[str, torch.Tensor]:
    """Fill an empty-weight module's params with small randoms (norms -> 1)."""
    sd: dict[str, torch.Tensor] = {}
    for k, v in module.state_dict().items():
        if k.endswith(".scale") and "norm" in k:
            sd[k] = torch.ones_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    return sd


def _build_ready(config, ops=None) -> Flux:
    ops = ops or _fp32_ops()
    m = Flux.from_config(config, ops)
    load_into_module(m, _randomised_state_dict(m), match_model_spec(config))
    m.eval()
    return m


# --- (a) construction + forward smoke ------------------------------------

def test_tiny_flux2_forward_shape():
    m = _build_ready(TINY_FLUX2)
    x = torch.randn(1, 16, 16, 16)
    out = m(x, torch.tensor([0.5]), torch.randn(1, 7, 32))
    assert out.shape == (1, 16, 16, 16)
    assert torch.isfinite(out).all()


def test_expand_attention_mask_shape_and_semantics():
    # (B, S_txt) key-padding mask -> bool (B, 1, 1, S_txt + img_tokens).
    token_mask = torch.tensor([[1, 1, 0], [1, 0, 0]], dtype=torch.long)
    expanded = Flux._expand_attention_mask(token_mask, img_tokens=5)
    assert expanded is not None
    assert expanded.shape == (2, 1, 1, 3 + 5)
    assert expanded.dtype == torch.bool
    # text positions reflect the mask; image positions are always valid.
    assert expanded[0, 0, 0, :3].tolist() == [True, True, False]
    assert expanded[1, 0, 0, :3].tolist() == [True, False, False]
    assert expanded[:, 0, 0, 3:].all()


def test_expand_attention_mask_short_circuits_when_nothing_masked():
    # all-valid mask -> None (keeps the accelerated no-mask attention path).
    assert Flux._expand_attention_mask(torch.ones(2, 4, dtype=torch.long), img_tokens=5) is None
    # no mask -> None.
    assert Flux._expand_attention_mask(None, img_tokens=5) is None


def test_masked_forward_differs_and_all_valid_matches_unmasked():
    m = _build_ready(TINY_FLUX2)
    x = torch.randn(1, 16, 16, 16)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32)
    with torch.no_grad():
        base = m(x, t, ctx)
        # an all-valid mask must be identical to passing no mask (None path).
        all_valid = m(x, t, ctx, attention_mask=torch.ones(1, 7, dtype=torch.long))
        # masking real text tokens out changes the attention -> output differs.
        partial = torch.ones(1, 7, dtype=torch.long)
        partial[0, 3:] = 0
        masked = m(x, t, ctx, attention_mask=partial)
    assert torch.equal(base, all_valid)
    assert not torch.allclose(base, masked)
    assert masked.shape == (1, 16, 16, 16) and torch.isfinite(masked).all()


def test_masked_forward_accepts_int64_mask():
    # regression: the tokenizer emits an int64 key-padding mask; it must not reach
    # sdpa as int64 (the live GPU dtype error). _expand_attention_mask casts to bool.
    m = _build_ready(TINY_FLUX2)
    mask = torch.zeros(1, 7, dtype=torch.long)
    mask[0, :4] = 1
    with torch.no_grad():
        out = m(torch.randn(1, 16, 16, 16), torch.tensor([0.5]), torch.randn(1, 7, 32), attention_mask=mask)
    assert out.shape == (1, 16, 16, 16) and torch.isfinite(out).all()


def test_tiny_flux1_forward_shape_with_pooled_and_guidance():
    m = _build_ready(TINY_FLUX1)
    # flux1 patch_size 2: latent H/W must be even; y (pooled 768) + guidance used.
    x = torch.randn(1, 16, 16, 16)
    out = m(x, torch.tensor([0.5]), torch.randn(1, 5, 32),
            y=torch.randn(1, 768), guidance=torch.tensor([3.5]))
    assert out.shape == (1, 16, 16, 16)
    assert torch.isfinite(out).all()


def test_forward_batch_and_nonsquare():
    m = _build_ready(TINY_FLUX2)
    out = m(torch.randn(2, 16, 24, 16), torch.tensor([0.3, 0.7]), torch.randn(2, 4, 32))
    assert out.shape == (2, 16, 24, 16)


# --- (b) meta-device key-set parity vs the real Klein header --------------

def test_klein_meta_keyset_parity():
    keys_file = _FIXTURES / "flux2_klein_9b.keys.txt"
    ckpt_keys = set(keys_file.read_text().split())
    assert len(ckpt_keys) == 201

    ops = pick_operations(torch.bfloat16, torch.bfloat16)
    with torch.device("meta"):
        m = Flux.from_config(KLEIN_CONFIG, ops)
    built = set(m.state_dict().keys())

    assert built == ckpt_keys, (
        f"key-set mismatch: missing={sorted(ckpt_keys - built)} "
        f"extra={sorted(built - ckpt_keys)}"
    )


def test_klein_fp8_ops_same_keyset_as_bf16():
    """fp8 (Fp8ScaledLinear) must not surface scale buffers in state_dict."""
    with torch.device("meta"):
        bf16 = Flux.from_config(KLEIN_CONFIG, pick_operations(torch.bfloat16, torch.bfloat16))
        fp8 = Flux.from_config(KLEIN_CONFIG, pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    assert set(bf16.state_dict().keys()) == set(fp8.state_dict().keys())


# --- (c) from_config consumes the exact Klein detect dict -----------------

def test_from_config_consumes_klein_detect_dict():
    with torch.device("meta"):
        m = Flux.from_config(KLEIN_CONFIG, pick_operations(torch.bfloat16, torch.bfloat16))
    p = m.params
    assert p.image_model == "flux2"
    assert p.hidden_size == 4096 and p.num_heads == 32
    assert p.depth == 8 and p.depth_single_blocks == 24
    assert p.in_channels == 128 and p.out_channels == 128
    # variant-derived fields the detect dict does NOT carry:
    assert p.global_modulation is True
    assert p.mlp_silu_act is True
    assert p.ops_bias is False
    assert p.vec_in_dim is None
    assert p.txt_ids_dims == [3]
    # structural consequences on the module:
    assert m.vector_in is None
    assert isinstance(m.guidance_in, torch.nn.Identity)
    assert hasattr(m, "double_stream_modulation_img")
    assert m.double_blocks[0].modulation is False  # shared modulation


# --- (d) post_load smoke --------------------------------------------------

def test_post_load_is_noop_and_leaves_no_meta():
    m = _build_ready(TINY_FLUX2)
    # post_load already ran inside load_into_module; calling again is safe.
    assert m.post_load() is None
    for _, b in m.named_buffers():
        assert b.device.type != "meta"


# --- Flux1-vs-Flux2 branching --------------------------------------------

def test_flux1_branching_differs_from_flux2():
    with torch.device("meta"):
        f1 = Flux.from_config(TINY_FLUX1, _fp32_ops())
    p = f1.params
    assert p.global_modulation is False
    assert p.ops_bias is True
    assert p.vec_in_dim == 768
    assert p.txt_ids_dims == []
    assert f1.vector_in is not None
    assert isinstance(f1.guidance_in, torch.nn.Module) and not isinstance(f1.guidance_in, torch.nn.Identity)
    assert f1.double_blocks[0].modulation is True
    assert not hasattr(f1, "double_stream_modulation_img")


# --- fp8 scale-key popping through the integrity gate ---------------------

def test_fp8_scale_keys_popped_not_flagged_unexpected():
    ops8 = pick_operations(torch.float8_e4m3fn, torch.bfloat16)
    m = Flux.from_config(TINY_FLUX2, ops8)
    sd = _randomised_state_dict(m)
    big = [k for k in list(sd) if k.endswith(".weight")
           and any(t in k for t in ("qkv", "proj", "mlp", "linear1", "linear2", "img_in", "txt_in"))]
    for k in big:
        base = k[: -len(".weight")]
        sd[base + ".weight_scale"] = torch.tensor(1.0)
        sd[base + ".input_scale"] = torch.tensor(1.0)
    # must NOT raise on the sidecar keys (Fp8ScaledLinear pops them; spec allowlists the rest).
    load_into_module(m, sd, match_model_spec(TINY_FLUX2))
    assert m.double_blocks[0].img_attn.qkv.weight_scale is not None


# --- integrity gate still fires for real mismatches -----------------------

def test_bogus_key_still_raises():
    m = Flux.from_config(TINY_FLUX2, _fp32_ops())
    sd = _randomised_state_dict(m)
    sd["totally.bogus.key"] = torch.zeros(1)
    with pytest.raises(NativeEngineLoadIntegrityError):
        load_into_module(m, sd, match_model_spec(TINY_FLUX2))


# --- FluxParams validation -----------------------------------------------

def test_fluxparams_rejects_bad_axes_sum():
    bad = dict(TINY_FLUX2, axes_dim=[8, 8, 8, 9])  # sum 33 != head_dim 32
    with pytest.raises(ValueError, match="axes_dim"):
        FluxParams.from_detect_config(bad)


def test_fluxparams_rejects_indivisible_heads():
    bad = dict(TINY_FLUX2, num_heads=3)  # 64 % 3 != 0
    with pytest.raises(ValueError, match="divisible"):
        FluxParams.from_detect_config(bad)


def test_fluxparams_rejects_unknown_model():
    with pytest.raises(ValueError, match="image_model"):
        FluxParams.from_detect_config(dict(TINY_FLUX2, image_model="sdxl"))


def test_flux_is_native_arch_module():
    with torch.device("meta"):
        m = Flux.from_config(TINY_FLUX2, _fp32_ops())
    assert isinstance(m, NativeArchModule)
