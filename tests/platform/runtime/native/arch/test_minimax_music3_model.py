"""Tests for the MiniMax-Music3 flow-matching DiT + fused condition encoder.

Coverage: config validation, meta-device key-set parity against the REAL
``ai/minimax_music3/minimax_music3_dit_fp16_header.json`` header (cheap — meta
tensors carry no storage, so this runs at the checkpoint's real 36-layer/2048-hidden
scale), a shrunk-scale full assign-load dry run (same shrink strategy as
``test_minimax_h3_real_header_dry_run.py``: every field here is shape-derivable, so
nothing needs pinning to a real value the way MiniMax-H3's hardcoded fields do),
the condition encoder's frame->latent resampling shape, the GLU value/gate chunk
order, partial-RoPE dimension coverage, and the fused-qkv split order.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.arch.minimax_music3.model import (
    MINIMAX_MUSIC3_DIT,
    MiniMaxMusic3DitConfig,
    MiniMaxMusic3GLU,
    MiniMaxMusic3Model,
    latent_length,
)
from vendor.gpl.comfyui.ops import pick_operations

_REPO_ROOT = Path(__file__).resolve().parents[5]
_HEADER_PATH = _REPO_ROOT / "ai" / "minimax_music3" / "minimax_music3_dit_fp16_header.json"

# Exact config a detector would derive from the real header (every field here is
# shape-derivable from the checkpoint's own tensor shapes; see the module docstring's
# "must NOT touch detect/*.py" note — this dict stands in for what S1's eventual
# `unet_detect.py` branch produces, not a claim about its exact key names).
REAL_CONFIG = dict(
    image_model=MINIMAX_MUSIC3_DIT,
    in_channels=128, condition_dim=2048, condition_hidden_dim=4096, num_condition_layers=8,
    num_layers=36, num_attention_heads=32, attention_head_dim=64, ffn_inner_dim=8192,
    rotary_dim=32, fourier_dim=256,
)

# Shrunk for the full assign-load dry run: same key SET as the real header (num_layers
# kept small only to bound the number of per-layer key groups exercised, not because
# the key pattern itself changes with layer count), every dimension reduced.
TINY_CONFIG = dict(
    image_model=MINIMAX_MUSIC3_DIT,
    in_channels=4, condition_dim=6, condition_hidden_dim=8, num_condition_layers=8,
    num_layers=2, num_attention_heads=2, attention_head_dim=4, ffn_inner_dim=6,
    rotary_dim=2, fourier_dim=8,
)


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _load_real_header() -> dict:
    if not _HEADER_PATH.exists():
        pytest.skip(f"{_HEADER_PATH} not present")
    with _HEADER_PATH.open() as f:
        header = json.load(f)
    header.pop("__metadata__", None)
    return header


def _build_ready(config: dict) -> MiniMaxMusic3Model:
    m = MiniMaxMusic3Model.from_config(config, _fp32_ops())
    sd = {}
    for k, v in m.state_dict().items():
        sd[k] = v.clone() if not v.is_floating_point() else torch.randn_like(v) * 0.02
    m.load_state_dict(sd, strict=True, assign=True)
    m.requires_grad_(False)
    m.post_load()
    m.eval()
    return m


# --- config ------------------------------------------------------------------

def test_config_from_detect_config():
    c = MiniMaxMusic3DitConfig.from_detect_config(REAL_CONFIG)
    assert c.num_layers == 36
    assert c.inner_dim == 32 * 64
    assert c.concat_channels == 2 * 128 + 2048


def test_config_rejects_wrong_image_model():
    with pytest.raises(ValueError, match="image_model"):
        MiniMaxMusic3DitConfig.from_detect_config(dict(REAL_CONFIG, image_model="wan2.1"))


# --- meta-device key parity vs the real header --------------------------------

def test_state_dict_key_set_matches_real_header():
    """374/374 keys, meta device only — no real weight data needed for a key-set
    check, so this runs at the checkpoint's real scale cheaply."""
    header = _load_real_header()
    with torch.device("meta"):
        m = MiniMaxMusic3Model.from_config(REAL_CONFIG, _fp32_ops())
    assert set(m.state_dict().keys()) == set(header.keys())


def test_state_dict_shape_ranks_match_real_header():
    header = _load_real_header()
    with torch.device("meta"):
        m = MiniMaxMusic3Model.from_config(REAL_CONFIG, _fp32_ops())
    sd = m.state_dict()
    for key, tensor in sd.items():
        assert tensor.ndim == len(header[key]["shape"]), key


def test_bite_check_wrong_ff_naming_breaks_key_parity():
    """A regression sentinel for the checkpoint's own `ff.ff.0.proj`/`ff.ff.2`
    double-nesting: a plausible-looking flatter naming (`ff.proj_in`/`ff.proj_out`)
    would NOT match the real header, proving the parity test above actually
    discriminates layout, not just tensor count."""
    header = _load_real_header()
    with torch.device("meta"):
        m = MiniMaxMusic3Model.from_config(REAL_CONFIG, _fp32_ops())
    wrong_keys = {
        k.replace("ff.ff.0.proj", "ff.proj_in").replace("ff.ff.2", "ff.proj_out")
        for k in m.state_dict().keys()
    }
    assert wrong_keys != set(header.keys())


# --- full assign-load dry run (shrunk scale, real key set) --------------------

def test_shrunk_module_assign_loads_and_forwards():
    m = _build_ready(TINY_CONFIG)
    latents = torch.randn(1, TINY_CONFIG["in_channels"], 3)
    condition = torch.randn(1, 3, TINY_CONFIG["condition_dim"])
    timestep = torch.tensor([0.3])
    with torch.no_grad():
        out = m(latents, timestep, condition)
    assert out.shape == latents.shape
    assert torch.isfinite(out).all()


def test_shrunk_module_condition_encoder_end_to_end():
    m = _build_ready(TINY_CONFIG)
    frame_hiddens = torch.randn(1, 5, TINY_CONFIG["num_condition_layers"] * TINY_CONFIG["condition_hidden_dim"])
    with torch.no_grad():
        condition = m.encode_condition(frame_hiddens)
    assert condition.shape == (1, latent_length(5), TINY_CONFIG["condition_dim"])
    assert torch.isfinite(condition).all()


# --- condition encoder shape (real dims, per plan's example) ------------------

def test_condition_encoder_shape_five_frames():
    """5 frames of [8*4096] -> int(5*3.4453125) = 17 latents (real dims, per the
    plan's own worked example)."""
    m = MiniMaxMusic3Model.from_config(REAL_CONFIG, _fp32_ops())
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02)
    m.post_load()
    frame_hiddens = torch.randn(1, 5, 8 * 4096)
    with torch.no_grad():
        condition = m.encode_condition(frame_hiddens)
    assert condition.shape == (1, 17, 2048)


def test_latent_length_formula():
    assert latent_length(5) == 17
    assert latent_length(200) == 689
    assert latent_length(0) == 1  # floor(0) would be 0; minimum-1 clamp applies


# --- GLU value-first/gate-second -----------------------------------------------

def test_glu_value_first_gate_second():
    glu = MiniMaxMusic3GLU(4, 3, _fp32_ops())
    with torch.no_grad():
        glu.proj.weight.zero_()
        glu.proj.bias.zero_()
        # bias layout is [value(3) | gate(3)]; set the gate half to a large
        # negative constant so silu(gate) ~ 0 and the value half survives untouched
        # only if "value" really is the FIRST half being multiplied by ~0 silu(gate).
        glu.proj.bias[3:] = -50.0
        glu.proj.bias[:3] = 7.0
    out = glu(torch.zeros(1, 4))
    # value=7 (first half), gate=-50 (second half) -> silu(-50) ~ 0 -> out ~ 0.
    assert torch.allclose(out, torch.zeros_like(out), atol=1e-3)

    with torch.no_grad():
        glu.proj.bias[3:] = 50.0  # silu(50) ~ 50 -> out ~ value * 50 = 350
    out2 = glu(torch.zeros(1, 4))
    assert torch.allclose(out2, torch.full_like(out2, 7.0 * 50.0), atol=1e-1)


def test_bite_check_gate_first_value_second_would_fail():
    """If the chunk order were reversed (gate first, value second — diffusers'
    OWN unfused convention, wrong for this checkpoint per the module's provenance
    note), the same bias pattern above would silence the output instead of
    passing it through — proving the test above discriminates the order."""
    glu = MiniMaxMusic3GLU(4, 3, _fp32_ops())
    with torch.no_grad():
        glu.proj.weight.zero_()
        glu.proj.bias.zero_()
        glu.proj.bias[3:] = -50.0
        glu.proj.bias[:3] = 7.0

    def _wrong_order_forward(x):
        gate, value = glu.proj(x).chunk(2, dim=-1)  # reversed
        return value * F.silu(gate)

    out = _wrong_order_forward(torch.zeros(1, 4))
    # gate=7 (first half) -> silu(7)~7, value=-50 (second half) -> out ~ -350, NOT ~0.
    assert not torch.allclose(out, torch.zeros_like(out), atol=1e-3)


# --- fused qkv split + partial RoPE ---------------------------------------------

def test_fused_qkv_chunk_order_matches_separate_projections():
    m = _build_ready(TINY_CONFIG)
    block = m.diffusion_transformer.transformer.layers[0]
    attn = block.self_attn
    x = torch.randn(1, 3, TINY_CONFIG["num_attention_heads"] * TINY_CONFIG["attention_head_dim"])

    inner = TINY_CONFIG["num_attention_heads"] * TINY_CONFIG["attention_head_dim"]
    w = attn.to_qkv.weight
    wq, wk, wv = w[:inner], w[inner:2 * inner], w[2 * inner:]
    ref_q = F.linear(x, wq)
    ref_k = F.linear(x, wk)
    ref_v = F.linear(x, wv)

    q, k, v = attn.to_qkv(x).chunk(3, dim=-1)
    torch.testing.assert_close(q, ref_q)
    torch.testing.assert_close(k, ref_k)
    torch.testing.assert_close(v, ref_v)


def test_partial_rope_leaves_tail_dims_unrotated():
    """Only the first `rotary_dim` channels of each head rotate; the rest must be
    passed through byte-identical."""
    from src.platform.runtime.native.arch.minimax_music3.model import _apply_partial_rotary_emb

    rotary_dim = 2
    x = torch.randn(1, 3, 2, 4)  # (B, S, H, D=4), rotary_dim=2 < D
    cos = torch.rand(3, rotary_dim)
    sin = torch.rand(3, rotary_dim)
    out = _apply_partial_rotary_emb(x, cos, sin)
    torch.testing.assert_close(out[..., rotary_dim:], x[..., rotary_dim:])
    assert not torch.allclose(out[..., :rotary_dim], x[..., :rotary_dim])


# --- post_load rotary buffer -----------------------------------------------------

def test_post_load_recomputes_inv_freq_not_trusting_checkpoint_copy():
    m = MiniMaxMusic3Model.from_config(TINY_CONFIG, _fp32_ops())
    garbage = torch.full_like(m.diffusion_transformer.transformer.rotary_pos_emb.inv_freq, 999.0)
    sd = {k: (v.clone() if not v.is_floating_point() else torch.randn_like(v) * 0.02) for k, v in m.state_dict().items()}
    sd["diffusion_transformer.transformer.rotary_pos_emb.inv_freq"] = garbage
    m.load_state_dict(sd, strict=True, assign=True)
    m.post_load()
    assert not torch.allclose(
        m.diffusion_transformer.transformer.rotary_pos_emb.inv_freq, garbage
    )
    theta = TINY_CONFIG.get("rope_theta", 1e4)
    rotary_dim = TINY_CONFIG["rotary_dim"]
    expected = 1.0 / (theta ** (torch.arange(0, rotary_dim, 2, dtype=torch.float32) / rotary_dim))
    torch.testing.assert_close(m.diffusion_transformer.transformer.rotary_pos_emb.inv_freq, expected)
