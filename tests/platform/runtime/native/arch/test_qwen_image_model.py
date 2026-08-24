"""Tests for the vendored Qwen-Image MMDiT.

Coverage: tiny-config forward smoke (unpacked 5D latent in/out), meta-device
key-set parity vs the real 2512 header fixture, detection deriving the exact real
config, detect->spec->from_config roundtrip, variant handling (2512 index vs 2511
index-timestep-zero buffer), no detection collision with flux/flux2/krea2.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import torch
from torch import Tensor

from src.platform.runtime.native.arch.qwen_image.config import QwenImageConfig
from src.platform.runtime.native.arch.qwen_image.model import QwenImageDiT
from src.platform.runtime.native.base import NativeArchModule, load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.detect.unet_detect import detect_unet_config
from vendor.gpl.comfyui.ops import pick_operations

_FIXTURES = Path(__file__).parent / "fixtures"

# Exact config detect_unet_config derives from the real 2512 header.
REAL_CONFIG = {
    "image_model": "qwen_image", "in_channels": 64, "out_channels": 16,
    "inner_dim": 3072, "num_layers": 60, "num_attention_heads": 24,
    "attention_head_dim": 128, "joint_attention_dim": 3584, "patch_size": 2,
    "axes_dims_rope": (16, 56, 56), "theta": 10000,
    "default_ref_method": "index", "use_additional_t_cond": False,
}

# Tiny: keep head_dim == the arch constant 128 (axes_dims_rope (16,56,56) is a
# fixed arch constant the detector cannot shape-derive, so a smaller head_dim
# would make detect->config roundtrips inconsistent). Everything else is small
# (1 head, 2 layers, narrow context).
TINY = {
    "image_model": "qwen_image", "in_channels": 16, "out_channels": 4,
    "inner_dim": 128, "num_layers": 2, "num_attention_heads": 1,
    "attention_head_dim": 128, "joint_attention_dim": 12, "patch_size": 2,
    "axes_dims_rope": (16, 56, 56), "theta": 10000,
    "default_ref_method": "index", "use_additional_t_cond": False,
}


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _build_ready(config) -> QwenImageDiT:
    m = QwenImageDiT.from_config(config, _fp32_ops())
    sd = {}
    for k, v in m.state_dict().items():
        if k.endswith(".weight") and (".norm" in k or "txt_norm" in k):
            sd[k] = torch.ones_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, match_model_spec(config))
    m.eval()
    return m


# --- forward smoke --------------------------------------------------------

def test_tiny_forward_shape():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)  # (B, C, T, H, W)
    out = m(x, torch.tensor([0.5]), torch.randn(1, 5, 12), attention_mask=torch.ones(1, 5, dtype=torch.long))
    assert out.shape == (1, 4, 1, 8, 8)
    assert torch.isfinite(out).all()


def test_forward_nonsquare_batch_and_no_mask():
    m = _build_ready(TINY)
    out = m(torch.randn(2, 4, 1, 8, 12), torch.tensor([0.3, 0.7]), torch.randn(2, 6, 12))
    assert out.shape == (2, 4, 1, 8, 12)


# --- text-padding trim equivalence (roadmap 2.3: trim instead of mask) ----

def _reference_forward_full_mask(m: QwenImageDiT, x: Tensor, timestep: Tensor,
                                  context: Tensor, attention_mask: Tensor) -> Tensor:
    """Pre-trim behavior: keep the full (padded) text length and build the
    dense additive mask over it, exactly as ``QwenImageDiT.forward`` did
    before the 2.3 trim. Reimplemented against the same submodules/weights so
    it is a faithful "old code path" reference, not a re-derivation."""
    mask = attention_mask
    if mask is not None and not torch.is_floating_point(mask) and bool(mask.all()):
        mask = None
    if mask is not None and not torch.is_floating_point(mask):
        mask = (mask - 1).to(x.dtype) * torch.finfo(x.dtype).max

    hidden_states, img_ids, orig_shape = m.pack_latents(x)
    num_embeds = hidden_states.shape[1]

    txt_start = round(max(((x.shape[-1] + (m.patch_size // 2)) // m.patch_size) // 2,
                          ((x.shape[-2] + (m.patch_size // 2)) // m.patch_size) // 2))
    txt_ids = torch.arange(txt_start, txt_start + context.shape[1], device=x.device).reshape(1, -1, 1).repeat(x.shape[0], 1, 3)
    ids = torch.cat((txt_ids, img_ids), dim=1)
    rope = m.pe_embedder(ids).to(x.dtype)

    hidden_states = m.img_in(hidden_states)
    encoder_hidden_states = m.txt_in(m.txt_norm(context))
    temb = m.time_text_embed(timestep, hidden_states, None)

    for block in m.transformer_blocks:
        encoder_hidden_states, hidden_states = block(hidden_states, encoder_hidden_states, mask, temb, rope)

    hidden_states = m.proj_out(m.norm_out(hidden_states, temb))
    hidden_states = hidden_states[:, :num_embeds]
    return m.unpack_latents(hidden_states, orig_shape, x.shape[-2], x.shape[-1])


def test_trim_matches_full_length_masking_with_padding():
    """Padded text tokens are fully masked out of the image stream (and the
    text stream is discarded), so trimming them away must not change the
    output at all — only the compute spent on them."""
    torch.manual_seed(0)
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    t = torch.tensor([0.4])
    real_len = 3
    pad_len = 29
    context = torch.randn(1, real_len + pad_len, 12)
    mask = torch.zeros(1, real_len + pad_len, dtype=torch.long)
    mask[:, :real_len] = 1

    trimmed_out = m(x, t, context, attention_mask=mask)
    full_out = _reference_forward_full_mask(m, x, t, context, mask)

    assert trimmed_out.shape == full_out.shape
    torch.testing.assert_close(trimmed_out, full_out, atol=1e-5, rtol=1e-4)


def _spy_on_sdpa():
    """Patch torch's scaled_dot_product_attention to record the mask/shape the
    dispatcher actually invoked it with. Returns (seen dict, restore callable)."""
    seen = {}
    real_attn = torch.nn.functional.scaled_dot_product_attention

    def spy(q, k, v, attn_mask=None, **kw):
        seen["mask_was_none"] = attn_mask is None
        seen["seq_len"] = q.shape[2]
        return real_attn(q, k, v, attn_mask=attn_mask, **kw)

    import src.platform.runtime.native.attention as attn_mod
    orig = attn_mod.F.scaled_dot_product_attention
    attn_mod.F.scaled_dot_product_attention = spy
    return seen, lambda: setattr(attn_mod.F, "scaled_dot_product_attention", orig)


def test_trim_clears_mask_for_batch1_padded_prompt_regardless_of_alignment():
    """The common case: a single prompt whose real length (23) is deliberately
    NOT a multiple of anything. Text tokens aren't patchified and RoPE takes
    arbitrary per-token ids, so there's no alignment requirement — the trim is
    exact. Trimmed mask must be all-True -> None (sage/flash eligible), and the
    joint sequence shrinks from 40+16 to 23+16."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    t = torch.tensor([0.5])
    context = torch.randn(1, 40, 12)
    mask = torch.zeros(1, 40, dtype=torch.long)
    mask[:, :23] = 1

    seen, restore = _spy_on_sdpa()
    try:
        m(x, t, context, attention_mask=mask)
    finally:
        restore()

    assert seen["mask_was_none"] is True
    assert seen["seq_len"] == 23 + 16


def test_trim_keeps_additive_mask_for_ragged_batch():
    """Genuinely ragged batch (batch=2, different real lengths per row): the
    trim shrinks to the longest real length in the batch (8), but the shorter
    row (5) still has real padding inside that window — mask must NOT be
    dropped; it's rebuilt over the shorter (trimmed) window, not the original
    full length."""
    m = _build_ready(TINY)
    x = torch.randn(2, 4, 1, 8, 8)
    t = torch.tensor([0.5, 0.5])
    context = torch.randn(2, 10, 12)
    mask = torch.zeros(2, 10, dtype=torch.long)
    mask[0, :5] = 1
    mask[1, :8] = 1

    seen, restore = _spy_on_sdpa()
    try:
        m(x, t, context, attention_mask=mask)
    finally:
        restore()

    assert seen["mask_was_none"] is False
    # trimmed to 8 (longest real length in the batch) + 16 image tokens, not the original 10 + 16.
    assert seen["seq_len"] == 8 + 16


# --- non-right-padded masks must skip the trim (roadmap S15/#15) ----------

def test_left_padded_mask_skips_trim_and_matches_full_length_reference():
    """Trimming assumed right-padding (real tokens form a prefix at index 0).
    A left-padded row (real tokens at the END) must not be trimmed at all —
    trimming to `[:txt_len]` would keep the FIRST N positions, which are
    padding, silently dropping every real token. Verified against the
    untrimmed full-length + additive-mask reference: the two must match
    exactly (same equivalence check as the right-padded trim test above)."""
    torch.manual_seed(1)
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    t = torch.tensor([0.4])
    real_len = 3
    pad_len = 29
    context = torch.randn(1, real_len + pad_len, 12)
    mask = torch.zeros(1, real_len + pad_len, dtype=torch.long)
    mask[:, pad_len:] = 1  # left-padded: real tokens are the LAST real_len positions

    out = m(x, t, context, attention_mask=mask)
    reference = _reference_forward_full_mask(m, x, t, context, mask)
    torch.testing.assert_close(out, reference, atol=1e-5, rtol=1e-4)


def test_left_padded_mask_does_not_trim_sequence_length():
    """Same scenario, checked via the attention seq_len spy: a left-padded row
    must NOT shrink the joint sequence the way a right-padded one does — the
    guard falls back to the untrimmed full-length + additive-mask path."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    t = torch.tensor([0.5])
    context = torch.randn(1, 40, 12)
    mask = torch.zeros(1, 40, dtype=torch.long)
    mask[:, 17:] = 1  # left-padded: 23 real tokens at the end

    seen, restore = _spy_on_sdpa()
    try:
        m(x, t, context, attention_mask=mask)
    finally:
        restore()

    # Untrimmed: full 40 text tokens + 16 image tokens, not 23 + 16.
    assert seen["seq_len"] == 40 + 16
    assert seen["mask_was_none"] is False


def test_interior_padding_also_skips_trim():
    """A real token following a padding token ANYWHERE (not just fully
    left-padded — e.g. padding stuck mid-sequence) must trip the same guard."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    t = torch.tensor([0.5])
    context = torch.randn(1, 10, 12)
    mask = torch.tensor([[1, 1, 0, 0, 1, 1, 0, 0, 0, 0]], dtype=torch.long)  # real token follows a pad at index 4

    seen, restore = _spy_on_sdpa()
    try:
        m(x, t, context, attention_mask=mask)
    finally:
        restore()

    assert seen["seq_len"] == 10 + 16  # untrimmed


def test_right_padded_multi_row_batch_still_trims():
    """Sanity check the guard doesn't over-trigger: an ordinary right-padded
    batch (every row's real tokens form a prefix) must still trim as before."""
    m = _build_ready(TINY)
    x = torch.randn(2, 4, 1, 8, 8)
    t = torch.tensor([0.5, 0.5])
    context = torch.randn(2, 10, 12)
    mask = torch.zeros(2, 10, dtype=torch.long)
    mask[0, :5] = 1
    mask[1, :8] = 1

    seen, restore = _spy_on_sdpa()
    try:
        m(x, t, context, attention_mask=mask)
    finally:
        restore()

    assert seen["seq_len"] == 8 + 16  # trimmed to the batch's longest real length


# --- meta key-set parity vs the real 2512 header --------------------------

def test_qwen_image_2512_meta_keyset_parity():
    ckpt_keys = set((_FIXTURES / "qwen_image_2512.keys.txt").read_text().split())
    assert len(ckpt_keys) == 1933
    with torch.device("meta"):
        m = QwenImageDiT.from_config(REAL_CONFIG, pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    built = set(m.state_dict().keys())
    assert built == ckpt_keys, (
        f"missing={sorted(ckpt_keys - built)[:15]} extra={sorted(built - ckpt_keys)[:15]}")


def test_edit_variant_adds_index_timestep_zero_buffer():
    with torch.device("meta"):
        plain = QwenImageDiT.from_config(REAL_CONFIG, _fp32_ops())
        edit = QwenImageDiT.from_config(dict(REAL_CONFIG, default_ref_method="index_timestep_zero"), _fp32_ops())
    assert "__index_timestep_zero__" not in dict(plain.state_dict())
    assert "__index_timestep_zero__" in dict(edit.state_dict())


# --- detection ------------------------------------------------------------

def test_detect_real_shapes_exact_config():
    # Build a meta module at the real config, snapshot shapes as a synthetic sd.
    with torch.device("meta"):
        real = QwenImageDiT.from_config(REAL_CONFIG, pick_operations(torch.bfloat16, torch.bfloat16))
    sd = {k: torch.empty(tuple(v.shape), device="meta") for k, v in real.state_dict().items()}
    assert detect_unet_config(sd) == REAL_CONFIG


def test_detect_spec_from_config_roundtrip():
    with torch.device("meta"):
        seed = QwenImageDiT.from_config(TINY, _fp32_ops())
    sd = {k: torch.empty(tuple(v.shape), device="meta") for k, v in seed.state_dict().items()}
    config = detect_unet_config(sd)
    assert config["image_model"] == "qwen_image"
    spec = match_model_spec(config)
    assert spec.family == "qwen_image"
    assert spec.sampling_settings["shift"] == 1.15
    assert spec.sampling_settings["guidance"] == "cfg"  # true CFG; denoise's canonical mode name
    with torch.device("meta"):
        rebuilt = QwenImageDiT.from_config(config, _fp32_ops())
    assert set(rebuilt.state_dict().keys()) == set(seed.state_dict().keys())


def test_detection_no_collision_with_other_families():
    # flux2 signature must not detect as qwen_image.
    flux2 = {
        "double_stream_modulation_img.lin.weight": torch.empty(4, 4, device="meta"),
        "double_blocks.0.img_attn.norm.key_norm.scale": torch.empty(4, device="meta"),
        "img_in.weight": torch.empty(8, 8, device="meta"),
        "txt_in.weight": torch.empty(8, 8, device="meta"),
    }
    assert detect_unet_config(flux2)["image_model"] == "flux2"
    # krea2 signature must not detect as qwen_image.
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


# --- config validation + contract ----------------------------------------

def test_config_rejects_bad_axes_sum():
    with pytest.raises(ValueError, match="axes_dims_rope"):
        QwenImageConfig.from_detect_config(dict(TINY, axes_dims_rope=(2, 2, 2)))


def test_config_rejects_inner_dim_mismatch():
    with pytest.raises(ValueError, match="inner_dim"):
        QwenImageConfig.from_detect_config(dict(TINY, inner_dim=24))


def test_is_native_arch_module_and_post_load_noop():
    with torch.device("meta"):
        m = QwenImageDiT.from_config(TINY, _fp32_ops())
    assert isinstance(m, NativeArchModule)
    assert m.post_load() is None


# --- ref_latents (Qwen-Image-Edit) -------------------------
#
# The concat-and-slice mechanism (pack each ref through the SAME pack_latents,
# concat onto the image-token axis with an incrementing temporal index, run
# joint attention over [txt | img | refs], slice back to :num_embeds before
# unpack) was already vendored dormant in this file before this task — see the
# module docstring. Cross-checked against ComfyUI's real
# comfy/ldm/qwen_image/model.py: main image index=0, refs index=1,2,... via the
# identical process_img/pack_latents call (no role branching), output sliced
# to num_embeds immediately after proj_out — all confirmed matching.
#
# This closed the remaining gap: the "index_timestep_zero" ref method (the
# 2511 edit checkpoint's actual default) additionally zeroes the ref tokens'
# timestep embedding — reference tokens are clean VAE latents, not noised
# samples, so they should never see the real step's timestep. Implemented by
# doubling `timestep`'s batch dim (real rows + zeroed rows) before the
# embedding, then each transformer block splits the resulting doubled `temb`
# back across the [real tokens | ref tokens] span — see
# `vendor/gpl/comfyui/qwen_image/layers.py`'s `_Block._modulate`/`_apply_gate`
# and `QwenImageDiT.forward`'s `timestep_zero_index` plumbing. The plain
# "index" method (2512 t2i, and any non-"index_timestep_zero" ref usage) is
# untouched — one shared timestep for every token, as before.

_REPO_ROOT = Path(__file__).resolve().parents[5]
_MODEL_PATH = "src/platform/runtime/native/arch/qwen_image/model.py"


def _load_head_qwen_image_model():
    """Import the pre-stage-2 ``model.py`` (as of ``HEAD``) as its own module,
    relative imports resolved against the REAL (unchanged) sibling ``config.py``
    — proves byte-identity against the actual historical source, not a trusted
    diff read. Git stays read-only: no checkout, no stash, just ``git show``."""
    src = subprocess.check_output(
        ["git", "show", f"HEAD:{_MODEL_PATH}"], cwd=_REPO_ROOT, text=True,
    )
    name = "qwen_image_model_head_be105_reference"
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "src.platform.runtime.native.arch.qwen_image"
    sys.modules[name] = module
    exec(compile(src, f"<git show HEAD:{_MODEL_PATH}>", "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def head_qwen_image_model():
    try:
        yield _load_head_qwen_image_model()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"could not load HEAD's model.py via git show: {exc}")


def test_no_ref_forward_matches_head_bit_exact(head_qwen_image_model):
    torch.manual_seed(11)
    m_new = _build_ready(TINY)
    sd = {k: v.clone() for k, v in m_new.state_dict().items()}

    m_head = head_qwen_image_model.QwenImageDiT.from_config(TINY, _fp32_ops())
    load_into_module(m_head, {k: v.clone() for k, v in sd.items()}, match_model_spec(TINY))
    m_head.eval()

    x = torch.randn(1, 4, 1, 8, 8)
    context = torch.randn(1, 5, 12)
    t = torch.tensor([0.4])

    out_new = m_new(x, t, context)
    out_head = m_head(x, t, context)
    assert torch.equal(out_new, out_head)


def test_forward_with_ref_latents_returns_target_shape():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 8, 8)
    context = torch.randn(1, 5, 12)
    out = m(x, torch.tensor([0.5]), context, ref_latents=[ref])
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


def test_forward_with_ref_latents_list_two_sources():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    refs = [torch.randn(1, 4, 1, 8, 8), torch.randn(1, 4, 1, 8, 8)]
    context = torch.randn(1, 5, 12)
    out = m(x, torch.tensor([0.5]), context, ref_latents=refs)
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


def test_ref_latents_changes_output_vs_no_ref():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 8, 8)
    context = torch.randn(1, 5, 12)
    t = torch.tensor([0.5])
    out_no_ref = m(x, t, context)
    out_ref = m(x, t, context, ref_latents=[ref])
    assert not torch.allclose(out_no_ref, out_ref)


def test_ref_tokens_get_incrementing_temporal_index_not_zero():
    """Main image tokens keep temporal id 0 (pack_latents' own default);
    successive refs get 1, 2, ... — ComfyUI's process_img index convention,
    no role branching (same function, just a different index argument)."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)          # 4x4 patch grid -> 16 target tokens
    refs = [torch.randn(1, 4, 1, 8, 8), torch.randn(1, 4, 1, 8, 8)]

    _hidden, target_ids, _orig = m.pack_latents(x)
    assert (target_ids[..., 0] == 0.0).all()

    _k1, ref1_ids, _ = m.pack_latents(refs[0], index=1)
    _k2, ref2_ids, _ = m.pack_latents(refs[1], index=2)
    assert (ref1_ids[..., 0] == 1.0).all()
    assert (ref2_ids[..., 0] == 2.0).all()


def test_differently_shaped_ref_is_allowed_unlike_krea2():
    """Qwen-Image-Edit's refs are patchified independently (their own grid,
    own token count) — no equal-grid requirement, unlike Krea-2's build_stream_
    inputs (which raises on a mismatched ref grid). A differently-sized
    reference is real-world normal (source photo != generation resolution)."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 16, 24)  # different H/W than the target
    context = torch.randn(1, 5, 12)
    out = m(x, torch.tensor([0.5]), context, ref_latents=[ref])
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


def test_output_token_count_excludes_ref_tokens():
    """hidden_states is sliced to :num_embeds (captured BEFORE any ref is
    concatenated) immediately after proj_out — the ref tokens must never leak
    into unpack_latents, or the output shape/content would be corrupted."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 32, 32)  # a much bigger ref -> many more ref tokens
    context = torch.randn(1, 5, 12)
    out = m(x, torch.tensor([0.5]), context, ref_latents=[ref])
    assert out.shape == x.shape  # still exactly the target's shape, not inflated


def test_index_timestep_zero_differentiates_from_plain_index():
    """'index' and 'index_timestep_zero' must now produce DIFFERENT
    output — the gap the block comment above documents is closed."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 8, 8)
    context = torch.randn(1, 5, 12)
    t = torch.tensor([0.5])
    out_index = m(x, t, context, ref_latents=[ref], ref_latents_method="index")
    out_tz = m(x, t, context, ref_latents=[ref], ref_latents_method="index_timestep_zero")
    assert not torch.allclose(out_index, out_tz)


def test_index_method_with_ref_latents_matches_head_bit_exact(head_qwen_image_model):
    """Regression guard: this only changes behavior for the
    "index_timestep_zero" ref method — plain "index" with ref_latents present
    (Kontext-style, no timestep zeroing) must stay byte-identical to HEAD."""
    torch.manual_seed(12)
    m_new = _build_ready(TINY)
    sd = {k: v.clone() for k, v in m_new.state_dict().items()}

    m_head = head_qwen_image_model.QwenImageDiT.from_config(TINY, _fp32_ops())
    load_into_module(m_head, {k: v.clone() for k, v in sd.items()}, match_model_spec(TINY))
    m_head.eval()

    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 8, 8)
    context = torch.randn(1, 5, 12)
    t = torch.tensor([0.4])

    out_new = m_new(x, t, context, ref_latents=[ref], ref_latents_method="index")
    out_head = m_head(x, t, context, ref_latents=[ref], ref_latents_method="index")
    assert torch.equal(out_new, out_head)


def test_index_timestep_zero_doubles_timestep_with_zeroed_second_half():
    """The mechanism's entry point: `timestep` fed into `time_text_embed` must
    be doubled to [real_timestep_row, zero_row] per batch item — the real row
    carries the actual step timestep unchanged, the second is exactly zero."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 8, 8)
    context = torch.randn(1, 5, 12)
    t = torch.tensor([0.5])

    seen = {}
    orig = m.time_text_embed.forward

    def spy(timestep, hidden_states, additional_t_cond=None):
        seen["timestep"] = timestep.clone()
        return orig(timestep, hidden_states, additional_t_cond)

    m.time_text_embed.forward = spy
    try:
        m(x, t, context, ref_latents=[ref], ref_latents_method="index_timestep_zero")
    finally:
        m.time_text_embed.forward = orig

    assert seen["timestep"].shape == (2,)
    torch.testing.assert_close(seen["timestep"][:1], t)
    assert torch.equal(seen["timestep"][1:], torch.zeros_like(t))


def test_plain_index_method_does_not_double_timestep():
    """Guard on the guard: the "index" method (default for 2512 t2i, and any
    non-"index_timestep_zero" ref usage) must never double the timestep batch —
    only "index_timestep_zero" triggers it."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 8, 8)
    context = torch.randn(1, 5, 12)
    t = torch.tensor([0.5])

    seen = {}
    orig = m.time_text_embed.forward

    def spy(timestep, hidden_states, additional_t_cond=None):
        seen["timestep"] = timestep.clone()
        return orig(timestep, hidden_states, additional_t_cond)

    m.time_text_embed.forward = spy
    try:
        m(x, t, context, ref_latents=[ref], ref_latents_method="index")
    finally:
        m.time_text_embed.forward = orig

    assert seen["timestep"].shape == (1,)
    assert torch.equal(seen["timestep"], t)


def test_txt2img_no_ref_latents_never_doubles_timestep():
    """No ref_latents at all (plain txt2img/img2img path) must never touch the
    timestep_zero_index machinery — `timestep_zero_index` stays None end to
    end regardless of the checkpoint's `default_ref_method`."""
    m = _build_ready(dict(TINY, default_ref_method="index_timestep_zero"))
    x = torch.randn(1, 4, 1, 8, 8)
    context = torch.randn(1, 5, 12)
    t = torch.tensor([0.5])

    seen = {}
    orig = m.time_text_embed.forward

    def spy(timestep, hidden_states, additional_t_cond=None):
        seen["timestep"] = timestep.clone()
        return orig(timestep, hidden_states, additional_t_cond)

    m.time_text_embed.forward = spy
    try:
        m(x, t, context)
    finally:
        m.time_text_embed.forward = orig

    assert seen["timestep"].shape == (1,)


def test_block_modulate_splits_real_and_zero_timestep_spans():
    """Direct test of the split mechanism at the vendored _Block level (no
    full DiT needed — `_modulate`/`_apply_gate` are staticmethods). `mod`
    stacks two batches: rows [0:B) are the real-timestep modulation, rows
    [B:2B) the zero-timestep modulation. The split must apply the first to
    tokens before `timestep_zero_index` (the real/generated tokens) and the
    second to tokens from `timestep_zero_index` onward (the reference
    tokens) — an exact boundary, not a blend."""
    from vendor.gpl.comfyui.qwen_image.layers import _Block

    real_tokens, ref_tokens, dim = 3, 2, 4
    x = torch.randn(1, real_tokens + ref_tokens, dim)
    shift_real, scale_real, gate_real = 1.0, 2.0, 3.0
    shift_zero, scale_zero, gate_zero = 10.0, 20.0, 30.0
    mod_real = torch.tensor([[shift_real] * dim + [scale_real] * dim + [gate_real] * dim])
    mod_zero = torch.tensor([[shift_zero] * dim + [scale_zero] * dim + [gate_zero] * dim])
    mod = torch.cat([mod_real, mod_zero], dim=0)  # (2B, 3*dim)

    out, gate = _Block._modulate(x, mod, timestep_zero_index=real_tokens)

    expected_real = shift_real + x[:, :real_tokens] * (1 + scale_real)
    expected_zero = shift_zero + x[:, real_tokens:] * (1 + scale_zero)
    torch.testing.assert_close(out[:, :real_tokens], expected_real)
    torch.testing.assert_close(out[:, real_tokens:], expected_zero)

    gate_r, gate_z = gate
    assert torch.allclose(gate_r, torch.full_like(gate_r, gate_real))
    assert torch.allclose(gate_z, torch.full_like(gate_z, gate_zero))

    y = torch.zeros_like(x)
    applied = _Block._apply_gate(x, y, gate, timestep_zero_index=real_tokens)
    torch.testing.assert_close(applied[:, :real_tokens], x[:, :real_tokens] * gate_real)
    torch.testing.assert_close(applied[:, real_tokens:], x[:, real_tokens:] * gate_zero)


def test_block_modulate_none_index_matches_pre_be111_behavior():
    """`timestep_zero_index=None` (the default, used by every non-edit and
    non-index_timestep_zero path) must still take the original single-batch
    branch — no accidental split when there's nothing to split."""
    from vendor.gpl.comfyui.qwen_image.layers import _Block

    dim = 4
    x = torch.randn(1, 5, dim)
    mod = torch.randn(1, 3 * dim)
    out, gate = _Block._modulate(x, mod, timestep_zero_index=None)
    shift, scale, expected_gate = torch.chunk(mod, 3, dim=-1)
    expected_out = torch.addcmul(shift.unsqueeze(1), x, 1 + scale.unsqueeze(1))
    torch.testing.assert_close(out, expected_out)
    torch.testing.assert_close(gate, expected_gate.unsqueeze(1))
