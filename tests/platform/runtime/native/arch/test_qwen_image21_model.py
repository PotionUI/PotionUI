"""Tests for the vendored Qwen-Image-2.1 single-stream DiT.

Coverage: tiny-config forward shape/dtype, block-causal mask construction
(text causal, image-block bidirectional, key-padding), modulation row
selection (prefix t=0 vs target real-timestep), detection from a synthetic
state dict (fused and diffusers-split MLP), meta key-set parity vs the real
Comfy-Org header fixture, detect->spec->from_config roundtrip, no detection
collision with Qwen-Image (1.0) or the other native families, and the
split->fused MLP state-dict conversion.
"""

from __future__ import annotations

from pathlib import Path

import torch

from src.platform.runtime.native.arch.qwen_image21.config import QwenImage21Config
from src.platform.runtime.native.arch.qwen_image21.model import (
    QwenImage21DiT,
    _block_causal_mask,
    _split_rows,
    convert_qwen_image21_state_dict,
)
from src.platform.runtime.native.base import NativeArchModule, load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from vendor.gpl.comfyui.ops import pick_operations

import pytest

_FIXTURES = Path(__file__).parent / "fixtures"

# Exact config detect_unet_config derives from the real Comfy-Org header.
REAL_CONFIG = {
    "image_model": "qwen_image21", "in_channels": 64, "out_channels": 64,
    "inner_dim": 4096, "num_layers": 32, "num_attention_heads": 32,
    "attention_head_dim": 128, "joint_attention_dim": 4096, "mlp_ratio": 3,
    "axes_dims_rope": (16, 56, 56), "theta": 10000, "fused_mlp": True,
}

# Tiny: keep head_dim == the arch constant 128 (axes_dims_rope (16,56,56) is a
# fixed arch constant the detector cannot shape-derive, so a smaller head_dim
# would make detect->config roundtrips inconsistent). Everything else small.
TINY = {
    "image_model": "qwen_image21", "in_channels": 8, "out_channels": 8,
    "inner_dim": 128, "num_layers": 2, "num_attention_heads": 1,
    "attention_head_dim": 128, "joint_attention_dim": 12, "mlp_ratio": 3,
    "axes_dims_rope": (16, 56, 56), "theta": 10000, "fused_mlp": True,
}


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _build_ready(config) -> QwenImage21DiT:
    m = QwenImage21DiT.from_config(config, _fp32_ops())
    sd = {}
    for k, v in m.state_dict().items():
        if k.endswith(".weight") and "norm" in k:
            sd[k] = torch.ones_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, match_model_spec(config))
    m.eval()
    return m


# --- forward smoke ----------------------------------------------------------

def test_forward_keeps_a_5d_latent_rank():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 1, 4, 4)
    out = m(x, torch.tensor([0.5]), torch.randn(1, 5, 12), attention_mask=torch.ones(1, 5, dtype=torch.long))
    assert out.shape == x.shape
    assert (x + 0.1 * out).shape == x.shape


def test_tiny_forward_shape_and_dtype():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    out = m(x, torch.tensor([0.5]), torch.randn(1, 5, 12), attention_mask=torch.ones(1, 5, dtype=torch.long))
    assert out.shape == (1, 8, 4, 4)
    assert out.dtype == torch.float32
    assert torch.isfinite(out).all()


def test_forward_multi_batch_no_mask():
    m = _build_ready(TINY)
    out = m(torch.randn(2, 8, 4, 6), torch.tensor([0.3, 0.7]), torch.randn(2, 6, 12))
    assert out.shape == (2, 8, 4, 6)
    assert torch.isfinite(out).all()


def test_forward_nonsquare_image():
    m = _build_ready(TINY)
    out = m(torch.randn(1, 8, 3, 5), torch.tensor([0.5]), torch.randn(1, 4, 12))
    assert out.shape == (1, 8, 3, 5)


# --- block-causal mask --------------------------------------------------

def test_mask_text_run_is_strictly_causal():
    # 3 text tokens (-1) then 4 image tokens (block 0, the target).
    token_kind = torch.tensor([-1, -1, -1, 0, 0, 0, 0])
    mask = _block_causal_mask(token_kind, None)
    text = mask[0, 0, :3, :3]
    assert torch.equal(text, torch.tril(torch.ones(3, 3, dtype=torch.bool)))


def test_mask_image_block_is_bidirectional():
    token_kind = torch.tensor([-1, -1, -1, 0, 0, 0, 0])
    mask = _block_causal_mask(token_kind, None)
    image_block = mask[0, 0, 3:, 3:]
    assert torch.equal(image_block, torch.ones(4, 4, dtype=torch.bool))


def test_mask_image_tokens_see_all_preceding_text():
    token_kind = torch.tensor([-1, -1, -1, 0, 0, 0, 0])
    mask = _block_causal_mask(token_kind, None)
    assert torch.equal(mask[0, 0, 3:, :3], torch.ones(4, 3, dtype=torch.bool))


def test_mask_text_never_sees_future_image_tokens():
    # Text tokens precede the image block in the sequence, so no image token
    # is a valid key for them under either the causal or same-block clause.
    token_kind = torch.tensor([-1, -1, -1, 0, 0, 0, 0])
    mask = _block_causal_mask(token_kind, None)
    assert not mask[0, 0, :3, 3:].any()


def test_mask_two_image_blocks_do_not_attend_bidirectionally_to_each_other():
    # ref image (block 0) then target image (block 1): the target may see the
    # whole ref block (causal, ref precedes it), but the ref block must NOT
    # see the (later) target block bidirectionally, and the two blocks are
    # never "same_block" with each other.
    token_kind = torch.tensor([0, 0, 1, 1])
    mask = _block_causal_mask(token_kind, None)
    assert torch.equal(mask[0, 0, 2:, :2], torch.ones(2, 2, dtype=torch.bool))  # target sees ref (causal)
    assert not mask[0, 0, :2, 2:].any()  # ref does not see target


def test_mask_applies_key_padding_per_batch_row():
    token_kind = torch.tensor([-1, -1, -1, 0, 0])
    key_valid = torch.tensor([[True, True, False], [True, True, True]])
    key_valid = torch.cat([key_valid, torch.ones(2, 2, dtype=torch.bool)], dim=1)
    mask = _block_causal_mask(token_kind, key_valid)
    assert mask.shape == (2, 1, 5, 5)
    assert not mask[0, 0, :, 2].any()   # row 0: padded text key masked for every query
    # row 1: key index 2 is real; every query at or after it (causal) sees it.
    assert mask[1, 0, 2:, 2].all()
    assert not mask[1, 0, :2, 2].any()  # earlier queries still excluded by causality, not padding


def test_key_padding_does_not_affect_query_rows():
    # A padded position stays a valid QUERY row (never fully masked out) --
    # only excluded as a KEY.
    token_kind = torch.tensor([-1, -1, 0])
    key_valid = torch.tensor([[True, False, True]])
    mask = _block_causal_mask(token_kind, key_valid)
    assert mask[0, 0, 1, 0]  # padded query at index 1 can still see the earlier real text token


# --- modulation row selection (t=0 prefix vs real-timestep target) ---------

def test_split_rows_prefix_is_shared_trailing_row():
    dim = 4
    real = torch.tensor([[1.0] * dim, [2.0] * dim])   # B=2 real rows
    zero = torch.tensor([[9.0] * dim])                 # 1 shared t=0 row
    p = torch.cat([real, zero], dim=0)                 # (B+1, dim)
    prefix, target = _split_rows(p)
    assert prefix.shape == (1, 1, dim)
    assert target.shape == (2, 1, dim)
    assert torch.equal(prefix[:, 0, 0], torch.tensor([9.0]))
    assert torch.equal(target[:, 0, 0], torch.tensor([1.0, 2.0]))


def test_prefix_tokens_get_shared_zero_timestep_end_to_end():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    t = torch.tensor([0.5])

    seen = {}
    orig = m.time_text_embed.forward

    def spy(timestep, dtype):
        seen["timestep"] = timestep.clone()
        return orig(timestep, dtype)

    m.time_text_embed.forward = spy
    try:
        m(x, t, context)
    finally:
        m.time_text_embed.forward = orig

    assert seen["timestep"].shape == (2,)   # 1 real row + 1 shared zero row
    torch.testing.assert_close(seen["timestep"][:1], t)
    assert torch.equal(seen["timestep"][1:], torch.zeros_like(t))


def test_zero_timestep_row_shared_across_batch_not_doubled_per_item():
    m = _build_ready(TINY)
    x = torch.randn(3, 8, 4, 4)
    context = torch.randn(3, 5, 12)
    t = torch.tensor([0.1, 0.5, 0.9])

    seen = {}
    orig = m.time_text_embed.forward

    def spy(timestep, dtype):
        seen["timestep"] = timestep.clone()
        return orig(timestep, dtype)

    m.time_text_embed.forward = spy
    try:
        m(x, t, context)
    finally:
        m.time_text_embed.forward = orig

    # B=3 real rows + exactly ONE shared zero row, not 3.
    assert seen["timestep"].shape == (4,)
    assert torch.equal(seen["timestep"][-1:], torch.zeros(1))


def test_prefix_and_target_modulation_differ():
    """Prefix (text) tokens must actually be affected by a DIFFERENT scale
    than target (image) tokens -- i.e. the split is load-bearing, not dead
    code that happens to produce the same output either way."""
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    t = torch.tensor([0.9])  # far from 0 so the two rows diverge
    out_t = m(x, t, context)
    out_near_zero = m(x, torch.tensor([1e-6]), context)
    assert not torch.allclose(out_t, out_near_zero)


# --- meta key-set parity vs the real Comfy-Org header ------------------

def test_qwen_image21_meta_keyset_parity():
    ckpt_keys = set((_FIXTURES / "qwen_image21.keys.txt").read_text().split())
    assert len(ckpt_keys) == 265
    with torch.device("meta"):
        m = QwenImage21DiT.from_config(REAL_CONFIG, pick_operations(torch.bfloat16, torch.bfloat16))
    built = set(m.state_dict().keys())
    assert built == ckpt_keys, (
        f"missing={sorted(ckpt_keys - built)[:15]} extra={sorted(built - ckpt_keys)[:15]}")


# --- detection --------------------------------------------------------------

def test_detect_real_shapes_exact_config():
    with torch.device("meta"):
        real = QwenImage21DiT.from_config(REAL_CONFIG, pick_operations(torch.bfloat16, torch.bfloat16))
    sd = {k: torch.empty(tuple(v.shape), device="meta") for k, v in real.state_dict().items()}
    assert detect_unet_config(sd) == REAL_CONFIG


def test_detect_spec_from_config_roundtrip():
    with torch.device("meta"):
        seed = QwenImage21DiT.from_config(TINY, _fp32_ops())
    sd = {k: torch.empty(tuple(v.shape), device="meta") for k, v in seed.state_dict().items()}
    config = detect_unet_config(sd)
    assert config["image_model"] == "qwen_image21"
    spec = match_model_spec(config)
    assert spec.family == "qwen_image21"
    assert spec.sampling_settings["shift"] == 2.0
    assert spec.sampling_settings["dynamic_shift"]["y2"] == 0.9
    assert spec.sampling_settings["guidance"] == "cfg"
    assert spec.vae_target == "qwen_image21"
    assert spec.clip_targets == ["qwen3vl_8b"]
    with torch.device("meta"):
        rebuilt = QwenImage21DiT.from_config(config, _fp32_ops())
    assert set(rebuilt.state_dict().keys()) == set(seed.state_dict().keys())


def test_detect_split_mlp_config_derives_fused_false():
    sd = {
        "img_in.weight": torch.empty(16, 8, device="meta"),
        "modulation.1.weight": torch.empty(4 * 16, 16, device="meta"),
        "txt_in.text_norm.weight": torch.empty(12, device="meta"),
        "txt_in.in_layer.weight": torch.empty(16, 12, device="meta"),
        "txt_in.out_layer.weight": torch.empty(16, 16, device="meta"),
        "proj_out.weight": torch.empty(8, 16, device="meta"),
        "transformer_blocks.0.attn.norm_q.weight": torch.empty(16, device="meta"),
        "transformer_blocks.0.img_mlp.gate_layer.weight": torch.empty(48, 16, device="meta"),
        "transformer_blocks.0.img_mlp.proj.weight": torch.empty(48, 16, device="meta"),
        "transformer_blocks.0.img_mlp.out.weight": torch.empty(16, 48, device="meta"),
    }
    config = detect_unet_config(sd)
    assert config["image_model"] == "qwen_image21"
    assert config["fused_mlp"] is False
    assert config["mlp_ratio"] == 3


def test_detection_no_collision_with_qwen_image_1_0():
    # 1.0's dual-stream add_q_proj signature must not detect as 2.1.
    qwen1 = {
        "transformer_blocks.0.attn.add_q_proj.weight": torch.empty(8, 8, device="meta"),
        "txt_norm.weight": torch.empty(8, device="meta"),
        "img_in.weight": torch.empty(8, 16, device="meta"),
        "txt_in.weight": torch.empty(8, 8, device="meta"),
        "proj_out.weight": torch.empty(16, 8, device="meta"),
        "transformer_blocks.0.attn.norm_q.weight": torch.empty(8, device="meta"),
    }
    assert detect_unet_config(qwen1)["image_model"] == "qwen_image"


def test_detection_no_collision_with_flux2_or_krea2():
    flux2 = {
        "double_stream_modulation_img.lin.weight": torch.empty(4, 4, device="meta"),
        "double_blocks.0.img_attn.norm.key_norm.scale": torch.empty(4, device="meta"),
        "img_in.weight": torch.empty(8, 8, device="meta"),
        "txt_in.weight": torch.empty(8, 8, device="meta"),
    }
    assert detect_unet_config(flux2)["image_model"] == "flux2"

    krea2 = {
        "txtfusion.projector.weight": torch.empty(1, 12, device="meta"),
        "blocks.0.attn.wq.weight": torch.empty(32, 32, device="meta"),
        "blocks.0.attn.wk.weight": torch.empty(16, 32, device="meta"),
        "blocks.0.attn.qknorm.qnorm.scale": torch.empty(16, device="meta"),
        "blocks.0.mlp.gate.weight": torch.empty(64, 32, device="meta"),
        "first.weight": torch.empty(32, 64, device="meta"),
        "txtmlp.1.weight": torch.empty(32, 16, device="meta"),
        "txtfusion.layerwise_blocks.0.attn.qknorm.qnorm.scale": torch.empty(8, device="meta"),
        "txtfusion.layerwise_blocks.0.attn.wk.weight": torch.empty(16, 16, device="meta"),
        "tmlp.0.weight": torch.empty(32, 16, device="meta"),
    }
    assert detect_unet_config(krea2)["image_model"] == "krea2"


# --- config validation + contract -------------------------------------

def test_config_rejects_bad_axes_sum():
    with pytest.raises(ValueError, match="axes_dims_rope"):
        QwenImage21Config.from_detect_config(dict(TINY, axes_dims_rope=(2, 2, 2)))


def test_config_rejects_inner_dim_mismatch():
    with pytest.raises(ValueError, match="inner_dim"):
        QwenImage21Config.from_detect_config(dict(TINY, inner_dim=24))


def test_config_rejects_wrong_image_model():
    with pytest.raises(ValueError, match="image_model"):
        QwenImage21Config.from_detect_config(dict(TINY, image_model="qwen_image"))


def test_is_native_arch_module_and_post_load_noop():
    with torch.device("meta"):
        m = QwenImage21DiT.from_config(TINY, _fp32_ops())
    assert isinstance(m, NativeArchModule)
    assert m.post_load() is None


def test_registered_spec_resolves_model_class():
    spec = match_model_spec({"image_model": "qwen_image21"})
    assert spec.resolve_model_class() is QwenImage21DiT


# --- ref_latents plumbing (dormant path; edit ships later) -----------------

def test_forward_with_single_ref_latent_returns_target_shape():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ref = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    out = m(x, torch.tensor([0.5]), context, ref_latents=[ref])
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


def test_ref_latents_are_modulated_from_zero_timestep_like_text():
    """A reference image's tokens sit in the prefix (before the target),
    so they must be modulated from t=0 too -- the model must not special-
    case "prefix" to mean "text only"."""
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ref = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    hidden_states, _pe, prefix_len, token_kind, _kv = m.build_sequence(x, context, None, [ref])
    text_len = 5
    ref_len = 16
    assert prefix_len == text_len + ref_len
    assert (token_kind[:text_len] == -1).all()
    assert (token_kind[text_len:text_len + ref_len] == 0).all()
    assert (token_kind[text_len + ref_len:] == 1).all()  # target is the last block


# --- image_slots splicing (Qwen-Image-Edit) ---------------------------

def test_slots_splice_ref_into_the_middle_of_the_text_run():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ref = torch.randn(1, 8, 2, 2)  # 4 tokens
    context = torch.randn(1, 6, 12)
    hidden_states, _pe, prefix_len, token_kind, _kv = m.build_sequence(
        x, context, None, [ref], image_slots=[3],
    )
    text_len, ref_len, target_len = 6, 4, 16
    assert hidden_states.shape[1] == text_len + ref_len + target_len
    assert prefix_len == text_len + ref_len
    # text[0:3], ref (block 0), text[3:6], target (block 1) last.
    assert (token_kind[:3] == -1).all()
    assert (token_kind[3:7] == 0).all()
    assert (token_kind[7:10] == -1).all()
    assert (token_kind[10:] == 1).all()


def test_no_slots_falls_back_to_ref_after_all_text_byte_identical_layout():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ref = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    with_slots = m.build_sequence(x, context, None, [ref], image_slots=None)
    without_slots_kw = m.build_sequence(x, context, None, [ref])
    for a, b in zip(with_slots, without_slots_kw):
        if isinstance(a, torch.Tensor):
            torch.testing.assert_close(a, b)
        else:
            assert a == b


def test_empty_slots_list_falls_back_to_after_text_same_as_none():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 2, 2)
    ref = torch.randn(1, 8, 2, 2)
    context = torch.randn(1, 3, 12)
    _hidden, _pe, prefix_len, token_kind, _kv = m.build_sequence(
        x, context, None, [ref], image_slots=[],
    )
    text_len, ref_len = 3, 4
    assert prefix_len == text_len + ref_len
    assert (token_kind[:3] == -1).all()
    assert (token_kind[3:7] == 0).all()


def test_two_refs_each_spliced_at_their_own_slot():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 2, 2)
    ref_a = torch.randn(1, 8, 1, 1)
    ref_b = torch.randn(1, 8, 1, 1)
    context = torch.randn(1, 4, 12)
    _hidden, _pe, prefix_len, token_kind, _kv = m.build_sequence(
        x, context, None, [ref_a, ref_b], image_slots=[1, 3],
    )
    # text[0:1], ref_a(0), text[1:3], ref_b(1), text[3:4], target(2).
    assert list(token_kind.tolist()) == [-1, 0, -1, -1, 1, -1, 2, 2, 2, 2]
    assert prefix_len == 4 + 1 + 1  # every prefix token: 4 text + 2 ref pixels


def test_slots_shorter_than_ref_latents_pads_remaining_refs_to_text_end():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 1, 1)
    ref_a = torch.randn(1, 8, 1, 1)
    ref_b = torch.randn(1, 8, 1, 1)
    context = torch.randn(1, 2, 12)
    _hidden, _pe, prefix_len, token_kind, _kv = m.build_sequence(
        x, context, None, [ref_a, ref_b], image_slots=[1],
    )
    # ref_a spliced at slot 1; ref_b has no slot -> falls back to text_len (2).
    assert list(token_kind.tolist()) == [-1, 0, -1, 1, 2]
    assert prefix_len == 2 + 1 + 1


def test_slots_key_valid_mask_follows_the_spliced_text_layout():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 1, 1)
    ref = torch.randn(1, 8, 1, 1)
    context = torch.randn(1, 3, 12)
    attention_mask = torch.tensor([[1, 0, 1]], dtype=torch.long)
    _hidden, _pe, _prefix_len, _tk, key_valid = m.build_sequence(
        x, context, attention_mask, [ref], image_slots=[1],
    )
    # text[0:1]=[1], ref(valid), text[1:3]=[0,1], target(valid).
    assert key_valid.tolist() == [[True, True, False, True, True]]


def test_forward_with_slots_end_to_end_matches_target_shape():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ref = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    out = m(x, torch.tensor([0.5]), context, ref_latents=[ref], image_slots=[2])
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


# --- split -> fused MLP state-dict conversion -------------------------

def test_convert_state_dict_fuses_split_mlp():
    gate = torch.arange(6.0).reshape(3, 2)
    up = torch.arange(6.0, 12.0).reshape(3, 2)
    sd = {
        "transformer_blocks.0.img_mlp.gate_layer.weight": gate,
        "transformer_blocks.0.img_mlp.proj.weight": up,
        "transformer_blocks.0.img_mlp.out.weight": torch.zeros(2, 3),
        "img_in.weight": torch.zeros(1, 1),
    }
    out = convert_qwen_image21_state_dict(sd)
    assert "transformer_blocks.0.img_mlp.gate_layer.weight" not in out
    assert "transformer_blocks.0.img_mlp.proj.weight" not in out
    fused = out["transformer_blocks.0.img_mlp.gate_up.weight"]
    assert fused.shape == (6, 2)
    torch.testing.assert_close(fused[:3], gate)
    torch.testing.assert_close(fused[3:], up)
    assert torch.equal(out["img_in.weight"], sd["img_in.weight"])  # untouched


def test_convert_state_dict_is_noop_on_fused_checkpoint():
    sd = {
        "transformer_blocks.0.img_mlp.gate_up.weight": torch.randn(6, 2),
        "transformer_blocks.0.img_mlp.out.weight": torch.randn(2, 3),
    }
    out = convert_qwen_image21_state_dict(sd)
    assert out.keys() == sd.keys()
    for k in sd:
        assert torch.equal(out[k], sd[k])


def test_convert_state_dict_fuses_multiple_blocks():
    sd = {}
    for i in range(3):
        sd[f"transformer_blocks.{i}.img_mlp.gate_layer.weight"] = torch.full((2, 2), float(i))
        sd[f"transformer_blocks.{i}.img_mlp.proj.weight"] = torch.full((2, 2), float(i + 10))
    out = convert_qwen_image21_state_dict(sd)
    for i in range(3):
        assert f"transformer_blocks.{i}.img_mlp.gate_up.weight" in out
        assert f"transformer_blocks.{i}.img_mlp.gate_layer.weight" not in out
