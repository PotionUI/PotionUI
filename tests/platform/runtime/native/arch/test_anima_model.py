"""Tests for the vendored Anima DiT (Cosmos-Predict2 MiniTrainDIT + LLMAdapter).

Coverage: tiny-config forward smoke (5D latent in/out + T5-fusion path), the
NOVEL ``post_load`` recompute (the 3D-RoPE range buffers + the LLMAdapter
``inv_freq`` are non-persistent and left garbage by meta construction — the first
native family whose ``post_load`` does real work), detection deriving the exact
real config, detect->spec->from_config roundtrip, LLMAdapter fusion output shape,
and no detection collision with the other DiT families.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.arch.anima.config import AnimaConfig
from src.platform.runtime.native.arch.anima.model import Anima
from src.platform.runtime.native.base import NativeArchModule, load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from vendor.gpl.comfyui.ops import pick_operations

# Exact config _detect_anima derives from the real anima_aestheticV10b header
# (2048-wide / 28-block t2i, in_channels 16).
REAL_CONFIG = {
    "image_model": "anima", "in_channels": 16, "out_channels": 16,
    "model_channels": 2048, "num_blocks": 28, "num_heads": 16,
    "crossattn_emb_channels": 1024, "patch_spatial": 2, "patch_temporal": 1,
    "concat_padding_mask": True, "mlp_ratio": 4.0, "use_adaln_lora": True,
    "adaln_lora_dim": 256, "max_img_h": 240, "max_img_w": 240, "max_frames": 128,
    "min_fps": 1, "max_fps": 30, "rope_h_extrapolation_ratio": 4.0,
    "rope_w_extrapolation_ratio": 4.0, "rope_t_extrapolation_ratio": 1.0,
    "rope_enable_fps_modulation": False, "llm_source_dim": 1024,
    "llm_target_dim": 1024, "llm_model_dim": 1024, "llm_num_layers": 6,
    "llm_num_heads": 16, "llm_vocab_size": 32128,
}

# Tiny: head_dim 16 (model_channels 32 / 2 heads), 2 blocks, tiny LLMAdapter.
TINY = {
    "image_model": "anima", "in_channels": 8, "out_channels": 8,
    "model_channels": 32, "num_blocks": 2, "num_heads": 2,
    "crossattn_emb_channels": 16, "patch_spatial": 2, "patch_temporal": 1,
    "concat_padding_mask": True, "mlp_ratio": 4.0, "use_adaln_lora": True,
    "adaln_lora_dim": 16, "max_img_h": 64, "max_img_w": 64, "max_frames": 8,
    "min_fps": 1, "max_fps": 30, "rope_h_extrapolation_ratio": 4.0,
    "rope_w_extrapolation_ratio": 4.0, "rope_t_extrapolation_ratio": 1.0,
    "rope_enable_fps_modulation": False, "llm_source_dim": 16,
    "llm_target_dim": 16, "llm_model_dim": 16, "llm_num_layers": 2,
    "llm_num_heads": 2, "llm_vocab_size": 100,
}


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _build_ready(config) -> Anima:
    """Build on meta, assign small random weights, load through the integrity gate
    (which runs ``post_load``), and return an eval-ready module."""
    with torch.device("meta"):
        m = Anima.from_config(config, _fp32_ops())
    sd = {}
    for k, v in m.state_dict().items():
        real = torch.empty(tuple(v.shape))
        if k.endswith(".weight") and ".norm" in k:
            sd[k] = real.fill_(1.0)
        elif v.is_floating_point():
            sd[k] = real.normal_(0.0, 0.02)
        else:
            sd[k] = real.zero_().to(v.dtype)
    load_into_module(m, sd, match_model_spec(config))
    return m.eval()


def _meta_shapes(config) -> dict:
    with torch.device("meta"):
        m = Anima.from_config(config, _fp32_ops())
    return {k: torch.empty(tuple(v.shape), device="meta") for k, v in m.state_dict().items()}


# --- forward smoke --------------------------------------------------------

def test_tiny_forward_shape_with_fusion():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 1, 16, 16)          # (B, C, T, H, W)
    context = torch.randn(1, 5, 16)           # Qwen3 hidden (source)
    t5_ids = torch.randint(0, 100, (1, 7))
    t5_w = torch.ones(1, 7)
    out = m(x, torch.tensor([0.7]), context, t5xxl_ids=t5_ids, t5xxl_weights=t5_w)
    assert out.shape == (1, 8, 1, 16, 16)
    assert torch.isfinite(out).all()


def test_attention_dispatcher_matches_direct_sdpa():
    # Anima's two attention modules route through the shared dispatcher
    # (src/platform/runtime/native/attention.py, injected into the vendored
    # layers module via set_attention_backend()) instead of calling
    # F.scaled_dot_product_attention directly. On CPU/fp32 the dispatcher always
    # falls back to its own sdpa path, so this pins that fallback to be bit-exact
    # with a direct F.scaled_dot_product_attention call on identical inputs -
    # i.e. the refactor is a pure routing change, not a numeric one.
    import torch.nn.functional as F

    import vendor.gpl.comfyui.anima.layers as anima_layers
    from src.platform.runtime.native.attention import attention as dispatch_attention
    from vendor.gpl.comfyui.anima.layers import _AdapterAttention, _Attention

    torch.manual_seed(0)
    ops = _fp32_ops()

    attn = _Attention(query_dim=32, context_dim=None, n_heads=4, head_dim=8, operations=ops)
    for p in attn.parameters():
        p.data.normal_(0.0, 0.02)
    x = torch.randn(1, 6, 32)

    def direct_sdpa(q, k, v):
        return F.scaled_dot_product_attention(q, k, v)

    orig_backend = anima_layers._attention_backend
    try:
        anima_layers.set_attention_backend(direct_sdpa)
        expected = attn(x)
        anima_layers.set_attention_backend(dispatch_attention)
        actual = attn(x)
    finally:
        anima_layers.set_attention_backend(orig_backend)
    torch.testing.assert_close(actual, expected)

    adapter_attn = _AdapterAttention(query_dim=16, context_dim=16, n_heads=2, head_dim=8, operations=ops)
    for p in adapter_attn.parameters():
        p.data.normal_(0.0, 0.02)
    ax = torch.randn(1, 4, 16)

    try:
        anima_layers.set_attention_backend(direct_sdpa)
        expected = adapter_attn(ax, None, None, None)
        anima_layers.set_attention_backend(dispatch_attention)
        actual = adapter_attn(ax, None, None, None)
    finally:
        anima_layers.set_attention_backend(orig_backend)
    torch.testing.assert_close(actual, expected)


def test_forward_accepts_and_ignores_generic_kwargs():
    # The generic engine model_forward passes y/guidance/attention_mask — Anima
    # accepts and ignores them (no vector input, embedded guidance, or DiT mask).
    m = _build_ready(TINY)
    out = m(torch.randn(1, 8, 1, 16, 16), torch.tensor([0.5]), torch.randn(1, 3, 16),
            y=None, guidance=torch.tensor([6.0]), attention_mask=torch.ones(1, 3, dtype=torch.long),
            t5xxl_ids=torch.randint(0, 100, (1, 4)), t5xxl_weights=torch.ones(1, 4))
    assert out.shape == (1, 8, 1, 16, 16) and torch.isfinite(out).all()


# --- the novel post_load recompute ---------------------------------------

def test_post_load_recomputes_nonpersistent_rope_buffers():
    with torch.device("meta"):
        m = Anima.from_config(TINY, _fp32_ops())
    # Before load, meta construction leaves the non-persistent buffers on meta.
    assert m.pos_embedder.dim_spatial_range.device.type == "meta"
    assert m.llm_adapter.rotary_emb.inv_freq.device.type == "meta"

    sd = {k: (torch.ones(tuple(v.shape)) if (k.endswith(".weight") and ".norm" in k)
              else torch.zeros(tuple(v.shape)).to(v.dtype) if not v.is_floating_point()
              else torch.randn(tuple(v.shape)) * 0.02)
          for k, v in m.state_dict().items()}
    load_into_module(m, sd, match_model_spec(TINY))  # runs post_load + no-meta assert

    # Real, finite, and equal to a fresh recompute.
    sr = m.pos_embedder.dim_spatial_range
    tr = m.pos_embedder.dim_temporal_range
    iv = m.llm_adapter.rotary_emb.inv_freq
    for b in (sr, tr, iv):
        assert b.device.type != "meta" and torch.isfinite(b).all()
    assert torch.equal(sr, m.pos_embedder._spatial_range())
    assert torch.equal(tr, m.pos_embedder._temporal_range())
    assert torch.equal(iv, m.llm_adapter.rotary_emb._inv_freq())


# --- LLMAdapter fusion ----------------------------------------------------

def test_preprocess_text_embeds_shape():
    m = _build_ready(TINY)
    context = torch.randn(1, 5, 16)           # Qwen3 hidden source
    t5_ids = torch.randint(0, 100, (1, 9))    # target sequence
    fused = m.preprocess_text_embeds(context, t5_ids)
    # Adapter output length == target (T5) sequence, width == llm_target_dim.
    assert fused.shape == (1, 9, 16) and torch.isfinite(fused).all()


# --- detection ------------------------------------------------------------

def test_detect_real_shapes_exact_config():
    assert detect_unet_config(_meta_shapes(REAL_CONFIG)) == REAL_CONFIG


def test_detect_spec_from_config_roundtrip():
    config = detect_unet_config(_meta_shapes(TINY))
    assert config["image_model"] == "anima"
    spec = match_model_spec(config)
    assert spec.family == "anima" and spec.variant == "anima"
    assert spec.sampling_settings["shift"] == 3.0
    assert spec.sampling_settings["guidance"] == "cfg"
    assert spec.clip_targets == ["qwen3_06b"]
    assert spec.vae_target == "qwen_image"
    with torch.device("meta"):
        rebuilt = Anima.from_config(config, _fp32_ops())
    assert set(rebuilt.state_dict().keys()) == set(_meta_shapes(TINY).keys())


def test_detection_no_collision_with_other_families():
    # An Anima checkpoint (has llm_adapter) must not be read as anything else, and
    # a plain flux2/qwen signature must not be read as anima.
    anima = _meta_shapes(TINY)
    assert detect_unet_config(anima)["image_model"] == "anima"
    flux2 = {
        "double_stream_modulation_img.lin.weight": torch.empty(4, 4, device="meta"),
        "double_blocks.0.img_attn.norm.key_norm.scale": torch.empty(4, device="meta"),
        "img_in.weight": torch.empty(8, 8, device="meta"),
        "txt_in.weight": torch.empty(8, 8, device="meta"),
    }
    assert detect_unet_config(flux2)["image_model"] == "flux2"


# --- contract -------------------------------------------------------------

def test_is_native_arch_module():
    with torch.device("meta"):
        m = Anima.from_config(TINY, _fp32_ops())
    assert isinstance(m, NativeArchModule)
    assert m.patch_size == TINY["patch_spatial"]  # engine helpers read `.patch_size`


def test_config_rejects_indivisible_heads():
    with pytest.raises(ValueError, match="num_heads"):
        AnimaConfig.from_detect_config(dict(REAL_CONFIG, num_heads=13))
