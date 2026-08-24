"""Tests for the Qwen3-VL-4B text encoder (Krea-2).

The language model is an ordinary Qwen3 (key parity is covered by
``test_key_parity``); these tests exercise the VL-specific contract: capture the
*output* of 12 specific layers (not Klein's before-layer capture), the
``(B, S', 12, hidden)`` layout, the template-prefix strip, and VL vs plain-Qwen3
detection routing.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.te_detect import detect_te_config
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from vendor.gpl.comfyui.ops import pick_operations
from src.platform.runtime.native.text_encoders.loader import _SPECS
from src.platform.runtime.native.text_encoders.qwen3 import (
    KREA2_LAYERS,
    Qwen3Model,
    Qwen3VLTextEncoder,
    _deepstack_inject,
    _trim_padded_tail,
)
from src.platform.runtime.native.text_encoders.qwen_vl_vision import qwen25vl_mrope_position_ids
from src.platform.runtime.native.text_encoders.qwen3_vl_vision import preprocess_qwen3_vl_image
from src.platform.runtime.native.text_encoders.tokenization import (
    KREA2VL_DEFAULT_SYSTEM_PROMPT,
    Qwen3VLTokenizer,
    krea2vl_image_prefix,
)

from . import _fixtures as fx

_REAL_VL = Path("models/text_encoders/qwen3vl_4b_fp8_scaled.safetensors")

# Tiny 36-layer Qwen3 (needs >= 35 layers to reach KREA2_LAYERS[-1] == 34).
_TINY_CFG = {
    "hidden_size": 16, "num_layers": 36, "vocab_size": 151936,
    "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 8,
    "intermediate_size": 32,
}


def _tiny_module() -> Qwen3Model:
    ops = pick_operations(torch.float32, torch.float32)
    m = Qwen3Model.from_config(_TINY_CFG, ops)
    sd = {}
    for k, v in m.state_dict().items():
        if k.endswith("norm.weight"):
            sd[k] = torch.ones_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    load_into_module(m, sd, _SPECS["qwen3"])
    m.eval()
    return m


def test_krea2_layers_are_twelve_outputs():
    assert KREA2_LAYERS == (1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34)
    assert len(KREA2_LAYERS) == 12


def test_capture_output_matches_manual_run():
    m = _tiny_module()
    ids = torch.tensor([[5, 6, 7]])
    x = m.model.embed_tokens(ids).float()
    cos, sin = m.model._rope(3, x.device, x.dtype)
    causal = torch.empty(3, 3).fill_(torch.finfo(x.dtype).min / 4).triu_(1)
    manual = []
    for i, layer in enumerate(m.model.layers):
        x = layer(x, cos, sin, causal)
        if i in set(KREA2_LAYERS):
            manual.append(x.clone())
    manual = torch.stack(manual, dim=1)  # [1,12,3,H]
    fwd = m(ids, attention_mask=torch.ones(1, 3, dtype=torch.long),
            layers_to_extract=KREA2_LAYERS, capture="output")
    assert torch.allclose(manual, fwd, atol=1e-4)


def test_capture_input_and_output_differ():
    m = _tiny_module()
    ids = torch.randint(0, 1000, (1, 4))
    mask = torch.ones(1, 4, dtype=torch.long)
    layers = (2, 5)
    before = m(ids, attention_mask=mask, layers_to_extract=layers, capture="input")
    after = m(ids, attention_mask=mask, layers_to_extract=layers, capture="output")
    assert not torch.allclose(before, after)


def test_capture_rejects_bad_mode():
    m = _tiny_module()
    with pytest.raises(ValueError, match="capture"):
        m(torch.zeros(1, 2, dtype=torch.long), layers_to_extract=(0,), capture="middle")


def test_encoder_output_shape_and_prefix_strip():
    m = _tiny_module()
    enc = Qwen3VLTextEncoder(m, Qwen3VLTokenizer(), device="cpu")
    out = enc.encode(["a cat", "a red car on a street"])
    ctx, mask = out["context"], out["attention_mask"]
    assert ctx.shape[0] == 2 and ctx.shape[2] == 12 and ctx.shape[3] == 16
    # layer axis at position 2 (Krea prepare_context attends across layers).
    assert ctx.shape[1] == mask.shape[1]  # sequence axes aligned after strip
    # prefix stripped -> shorter than the 512 min-length pad.
    assert ctx.shape[1] < 512


def test_tokenizer_prefix_len_matches_reference_constant():
    # diffusers' Krea2Pipeline hardcodes prompt_template_encode_start_idx = 34;
    # this tokenizer derives the same value from the vocab.
    assert Qwen3VLTokenizer()._prefix_len == 34


def test_detection_routes_vl_vs_plain():
    vl = {
        "model.embed_tokens.weight": torch.zeros(260, 16),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(8),
        "model.visual.blocks.0.attn.qkv.weight": torch.zeros(4, 4),
    }
    cfg = detect_te_config(vl)
    assert cfg["te_type"] == "qwen3vl" and cfg["variant"] == "qwen3vl_4b"

    plain = {
        "model.embed_tokens.weight": torch.zeros(260, 2560),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(8),
    }
    cfg = detect_te_config(plain)
    assert cfg["te_type"] == "qwen3" and cfg["variant"] == "qwen3_4b"


def test_vl_spec_registered_and_strips_visual():
    spec = _SPECS["qwen3vl"]
    assert spec.model_class is Qwen3Model
    # visual keys are allowlisted defensively (they are stripped before load).
    assert spec.key_is_expected_unexpected("model.visual.blocks.0.attn.qkv.weight")


# --- padded-tail trim (sage2/flash fallback fix) -------------------------

class TestTrimPaddedTail:
    def test_batch_one_trims_to_real_length_and_mask_becomes_all_ones(self):
        context = torch.randn(1, 10, 12, 16)
        mask = torch.ones(1, 10, dtype=torch.long)
        mask[0, 4:] = 0  # 4 real tokens, 6 padded

        trimmed_ctx, trimmed_mask = _trim_padded_tail(context, mask)

        assert trimmed_ctx.shape == (1, 4, 12, 16)
        assert trimmed_mask.shape == (1, 4)
        assert trimmed_mask.all()
        assert torch.equal(trimmed_ctx, context[:, :4])

    def test_unequal_batch_trims_to_the_longest_row(self):
        context = torch.randn(2, 10, 12, 16)
        mask = torch.ones(2, 10, dtype=torch.long)
        mask[0, 3:] = 0  # row 0: 3 real tokens
        mask[1, 7:] = 0  # row 1: 7 real tokens (the longest)

        trimmed_ctx, trimmed_mask = _trim_padded_tail(context, mask)

        assert trimmed_ctx.shape == (2, 7, 12, 16)
        assert trimmed_mask.shape == (2, 7)
        # row 0 keeps a real (now merely tighter) mask - still padded relative
        # to row 1, so this batch still exercises krea2's masked-attention path.
        assert trimmed_mask[0].tolist() == [1, 1, 1, 0, 0, 0, 0]
        assert trimmed_mask[1].tolist() == [1] * 7

    def test_no_padding_at_all_is_a_noop(self):
        context = torch.randn(1, 5, 12, 16)
        mask = torch.ones(1, 5, dtype=torch.long)
        trimmed_ctx, trimmed_mask = _trim_padded_tail(context, mask)
        assert torch.equal(trimmed_ctx, context)
        assert torch.equal(trimmed_mask, mask)


def test_encode_trims_the_512_min_length_pad_for_a_normal_prompt():
    """The actual bug: a real prompt is far short of the tokenizer's 512-token
    minimum, so `encode()`'s output must be trimmed down to the real content,
    not left at the full prefix-stripped-but-still-padded 478."""
    m = _tiny_module()
    enc = Qwen3VLTextEncoder(m, Qwen3VLTokenizer(), device="cpu")
    out = enc.encode(["a cat"])
    ctx, mask = out["context"], out["attention_mask"]

    assert mask.all()  # batch-1 -> fully real content, no padding left
    assert ctx.shape[1] == mask.shape[1]
    assert ctx.shape[1] < 100  # nowhere near the 478 prefix-stripped pad length
    assert ctx.shape[0] == 1 and ctx.shape[2] == 12 and ctx.shape[3] == 16


def test_encode_unequal_batch_keeps_a_real_mask_after_trim():
    m = _tiny_module()
    enc = Qwen3VLTextEncoder(m, Qwen3VLTokenizer(), device="cpu")
    out = enc.encode(["a cat", "a considerably longer prompt describing many more things in the scene"])
    ctx, mask = out["context"], out["attention_mask"]

    assert ctx.shape[1] == mask.shape[1]
    # trimmed to the longer row's real length - shorter row still has zeros.
    assert not mask[0].all()
    assert mask[1].all()


def test_encode_weighted_still_works_after_trim_fix():
    """Prompt weighting must not break: `_encode_ids` (used internally by
    `encode_weighted`) is deliberately left untrimmed - see `_trim_padded_tail`'s
    docstring for the baseline-alignment hazard this avoids."""
    m = _tiny_module()
    enc = Qwen3VLTextEncoder(m, Qwen3VLTokenizer(), device="cpu")
    out = enc.encode_weighted("a (red:1.4) car")
    assert torch.isfinite(out["context"]).all()


# --- vision-grounded instruction encode (Krea-2 edit mode) ---------

_TINY_VISION_CFG = {
    "vision_hidden_size": 8, "vision_intermediate_size": 16,
    "vision_num_layers": 2, "vision_num_heads": 2,
    "vision_patch_size": 2, "vision_temporal_patch_size": 2,
    "vision_spatial_merge_size": 2, "vision_num_position_embeddings": 16,
    "vision_deepstack_indexes": (0,),
}
_VL_CFG = {**_TINY_CFG, "vision": True, **_TINY_VISION_CFG}


def _tiny_vl_module() -> Qwen3Model:
    ops = pick_operations(torch.float32, torch.float32)
    m = Qwen3Model.from_config(_VL_CFG, ops)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02)
    for mod in m.modules():
        if isinstance(mod, torch.nn.LayerNorm):
            torch.nn.init.ones_(mod.weight)
            torch.nn.init.zeros_(mod.bias)
    m.post_load()
    m.eval()
    return m


def test_vision_false_default_has_no_visual_attribute():
    m = _tiny_module()
    assert not hasattr(m.model, "visual")


def test_vision_true_builds_visual_tower_under_model_prefix():
    m = _tiny_vl_module()
    assert hasattr(m.model, "visual")
    keys = set(m.state_dict())
    # Checkpoint prefix is `model.visual.*` (nested, unlike Qwen2.5-VL's
    # top-level `visual.*`) -- see qwen3_vl_vision.py's Qwen3VLVisionTower docstring.
    assert any(k.startswith("model.visual.") for k in keys)
    assert m.model.visual.merger.linear_fc2.out_features == _VL_CFG["hidden_size"]


# --- vision-grounded template layout -----------------------------------------


def test_tokenize_with_images_one_ref_layout():
    pytest.importorskip("transformers")
    tok = Qwen3VLTokenizer()
    ids, mask, prefix_len = tok.tokenize_with_images("make it night", num_images=1, device="cpu")
    text = tok._tok.decode(ids[0].tolist())
    assert text.count("<|vision_start|><|image_pad|><|vision_end|>") == 1
    assert text.startswith("<|im_start|>system")
    assert KREA2VL_DEFAULT_SYSTEM_PROMPT in text
    assert "make it night" in text
    assert text.rstrip().endswith("<|im_start|>assistant")

    expected_prefix = tok._tok(krea2vl_image_prefix(KREA2VL_DEFAULT_SYSTEM_PROMPT, 1))["input_ids"]
    assert prefix_len == len(expected_prefix)
    assert ids[0, :prefix_len].tolist() == expected_prefix


def test_tokenize_with_images_two_refs_layout():
    pytest.importorskip("transformers")
    tok = Qwen3VLTokenizer()
    ids, _mask, prefix_len = tok.tokenize_with_images("blend the scene and subject", num_images=2, device="cpu")
    text = tok._tok.decode(ids[0].tolist())
    assert text.count("<|vision_start|><|image_pad|><|vision_end|>") == 2

    expected_prefix = tok._tok(krea2vl_image_prefix(KREA2VL_DEFAULT_SYSTEM_PROMPT, 2))["input_ids"]
    assert prefix_len == len(expected_prefix)


def test_tokenize_with_images_system_prompt_override_changes_prefix_len_and_text():
    pytest.importorskip("transformers")
    tok = Qwen3VLTokenizer()
    _ids_a, _mask_a, default_len = tok.tokenize_with_images("x", num_images=1, device="cpu")
    ids_b, _mask_b, custom_len = tok.tokenize_with_images(
        "x", num_images=1, device="cpu", system_prompt="short custom prompt"
    )
    assert custom_len != default_len
    assert "short custom prompt" in tok._tok.decode(ids_b[0].tolist())


def test_tokenize_with_images_rejects_zero_images():
    pytest.importorskip("transformers")
    tok = Qwen3VLTokenizer()
    with pytest.raises(ValueError):
        tok.tokenize_with_images("x", num_images=0, device="cpu")


# --- m-RoPE id CONSTRUCTION vs upstream Qwen3-VL's own formula ---------------


def test_qwen3vl_image_position_ids_match_upstream_get_vision_position_ids_formula():
    """HF's Qwen3VLModel.get_rope_index (image branch) delegates to
    get_vision_position_ids(start_position, grid_thw, spatial_merge_size):
    T axis constant at `start_position` (`tpos_ids = arange(t); position[0] +=
    start_position` for a still image t=1 -> just `start_position`), H/W
    meshgridded over the post-merge grid and offset by `start_position`. This
    repo reuses `qwen25vl_mrope_position_ids` (verified equivalent for images
    in qwen3_vl_vision.py's module docstring) instead of re-deriving it --
    hand-derive HF's formula independently here and compare, for a small grid.
    """
    grid = torch.tensor([[1, 4, 4]])  # 4x4 patches -> post-merge 2x2
    start = 3
    spans = [(start, 4, grid)]
    ours = qwen25vl_mrope_position_ids(spans, seq_len=9, device="cpu")

    llm_h = llm_w = 4 // 2
    h_axis = torch.arange(llm_h) + start
    w_axis = torch.arange(llm_w) + start
    t_axis = torch.arange(1)
    tg, hg, wg = torch.meshgrid(t_axis, h_axis, w_axis, indexing="ij")
    hf_image_pos = torch.stack([tg, hg, wg], dim=0).reshape(3, -1)
    hf_image_pos[0] += start  # T axis: position[0] += start_position, per HF's code

    assert torch.equal(ours[:, start:start + 4], hf_image_pos)


# --- splice correctness + full image-conditioned encode ---------------------


def test_encode_images_without_vision_tower_raises():
    m = _tiny_module()
    enc = Qwen3VLTextEncoder(m, Qwen3VLTokenizer(), device="cpu")
    with pytest.raises(NativeEngineUnsupportedError):
        enc.encode(["a cat"], images=[torch.rand(32, 32, 3)])


# Unlike Qwen2.5-VL's FIXED image template (always exactly one
# `<|image_pad|>` slot, so a caller can hand it a mismatched image count),
# Krea-2's template is built with exactly `num_images=len(images)` vision
# blocks (`tokenize_with_images`) -- so `_encode_with_images`'s
# `len(pad_positions) != len(images)` guard can never actually fire through
# the public `encode()` path. It stays as defensive depth against a future
# tokenizer bug, not something reachable from here.


def test_encode_images_batch_greater_than_one_raises():
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    enc = Qwen3VLTextEncoder(m, Qwen3VLTokenizer(), device="cpu")
    with pytest.raises(ValueError, match="one prompt"):
        enc.encode(["a", "b"], images=[torch.rand(32, 32, 3)])


def test_encode_images_rejects_zero_images_at_call_site():
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    enc = Qwen3VLTextEncoder(m, Qwen3VLTokenizer(), device="cpu")
    # encode(images=[]) is falsy -> treated as no images, plain text-only path
    # (not an error) — the tokenizer-level `num_images=0` guard is exercised
    # directly by test_tokenize_with_images_rejects_zero_images above.
    out = enc.encode(["a cat"], images=[])
    assert set(out) == {"context", "attention_mask"}


def test_encode_with_image_shapes_and_finite():
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    enc = Qwen3VLTextEncoder(m, Qwen3VLTokenizer(), device="cpu")
    img = torch.rand(32, 32, 3)
    out = enc.encode(["make it night"], images=[img])
    assert set(out) == {"context", "attention_mask"}
    assert out["context"].shape[0] == 1
    assert out["context"].shape[2] == 12 and out["context"].shape[3] == 16
    assert out["context"].shape[1] == out["attention_mask"].shape[1]
    assert torch.isfinite(out["context"]).all()


def test_encode_with_image_splice_produces_expected_sequence_length():
    """Splice-correctness: the single <|image_pad|> placeholder must expand
    to EXACTLY the vision tower's merged-token count, not more or fewer.
    Derives the expected final length independently (same public tokenizer/
    preprocess entry points the encoder uses internally, but never touching
    the encoder's own internal splice bookkeeping) and compares.
    """
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    tok = Qwen3VLTokenizer()
    enc = Qwen3VLTextEncoder(m, tok, device="cpu")
    img = torch.rand(32, 32, 3)

    ids, _mask, prefix_len = tok.tokenize_with_images("make it night", num_images=1, device="cpu")
    visual = m.model.visual
    _patches, grid_thw = preprocess_qwen3_vl_image(
        img, grounding_px=768, patch_size=visual.patch_size,
        temporal_patch_size=visual.patch_embed.temporal_patch_size,
        merge_size=visual.spatial_merge_size,
    )
    merged_count = int((grid_thw[0, 1] * grid_thw[0, 2]) // (visual.spatial_merge_size ** 2))
    # -1 drops the single <|image_pad|> placeholder token the merged tokens replace.
    expected_len = ids.shape[1] - 1 + merged_count - prefix_len

    out = enc.encode(["make it night"], images=[img])
    assert out["context"].shape[1] == expected_len
    assert out["attention_mask"].shape[1] == expected_len


def test_encode_with_two_images_splice_length():
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    tok = Qwen3VLTokenizer()
    enc = Qwen3VLTextEncoder(m, tok, device="cpu")
    img_a, img_b = torch.rand(32, 32, 3), torch.rand(16, 16, 3)

    ids, _mask, prefix_len = tok.tokenize_with_images("blend them", num_images=2, device="cpu")
    visual = m.model.visual
    merged_total = 0
    for img in (img_a, img_b):
        _patches, grid_thw = preprocess_qwen3_vl_image(
            img, grounding_px=768, patch_size=visual.patch_size,
            temporal_patch_size=visual.patch_embed.temporal_patch_size,
            merge_size=visual.spatial_merge_size,
        )
        merged_total += int((grid_thw[0, 1] * grid_thw[0, 2]) // (visual.spatial_merge_size ** 2))
    expected_len = ids.shape[1] - 2 + merged_total - prefix_len  # -2 drops both placeholders

    out = enc.encode(["blend them"], images=[img_a, img_b])
    assert out["context"].shape[1] == expected_len
    assert out["attention_mask"].shape[1] == expected_len


def test_encode_with_image_mask_all_valid():
    """Every position in a single image-conditioned request is real content
    (no padding to another row in the batch)."""
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    enc = Qwen3VLTextEncoder(m, Qwen3VLTokenizer(), device="cpu")
    out = enc.encode(["make it night"], images=[torch.rand(32, 32, 3)])
    assert out["attention_mask"].all()


# --- DeepStack injection -----------------------------------------------------


def test_deepstack_inject_adds_only_at_masked_positions():
    x = torch.zeros(1, 5, 4)
    mask = torch.tensor([[False, True, True, False, False]])
    visual_embeds = torch.tensor([[1.0, 1.0, 1.0, 1.0], [2.0, 2.0, 2.0, 2.0]])

    out = _deepstack_inject(x, mask, visual_embeds)
    assert torch.equal(out[0, 0], torch.zeros(4))
    assert torch.equal(out[0, 1], torch.ones(4))
    assert torch.equal(out[0, 2], torch.full((4,), 2.0))
    assert torch.equal(out[0, 3], torch.zeros(4))
    assert torch.equal(out[0, 4], torch.zeros(4))


def test_deepstack_inject_does_not_mutate_input_in_place():
    x = torch.zeros(1, 3, 2)
    mask = torch.tensor([[True, False, False]])
    visual_embeds = torch.ones(1, 2)
    _out = _deepstack_inject(x, mask, visual_embeds)
    assert torch.equal(x, torch.zeros(1, 3, 2))  # original untouched


@pytest.mark.requires_models
def test_real_vl_lm_keyset_matches_qwen3_fixture():
    """The real VL checkpoint's language model (vision tower stripped) is an
    ordinary 36-layer Qwen3 — same key set as the QWEN3_4B fixture."""
    if not _REAL_VL.is_file():
        pytest.skip(f"real checkpoint not present: {_REAL_VL}")
    with open(_REAL_VL, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    lm_keys = {
        k for k in header
        if k != "__metadata__"
        and not k.startswith("model.visual.")
        and not (k.endswith(".weight_scale") or k.endswith(".input_scale") or k.endswith(".comfy_quant"))
    }
    assert lm_keys == fx.expand_keys(fx.QWEN3_4B)
