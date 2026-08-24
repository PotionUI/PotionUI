"""Tests for MiniMax-H3's Qwen3-VL-32B text encoder (native port stage S4).

Covers: config derivation for both the Comfy-Org trimmed (50-layer) and a full
(64-layer) checkpoint, the `hidden_states[50]` tap-index off-by-one, the
top-level vision-tower attachment point (32B differs from Krea-2's nested 4B),
the tokenizer/vision-run presentation primitives, and the encode contract
(no chat template, no padding, no pooling).
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.te_detect import detect_te_config
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from vendor.gpl.comfyui.ops import pick_operations
from src.platform.runtime.native.text_encoders.loader import _SPECS, _build_config, load_text_encoder
from src.platform.runtime.native.text_encoders.qwen3 import (
    MINIMAX_H3_TEXT_ENCODER_LAYER_INDEX,
    MINIMAX_H3_TEXT_TAG,
    MINIMAX_H3_VIDEO_TAG,
    MiniMaxH3Reference,
    MiniMaxH3TextEncoder,
    MiniMaxH3VisionRun,
    Qwen3Config,
    Qwen3Model,
    _find_pad_run,
)
from src.platform.runtime.native.text_encoders.qwen3_vl_vision import (
    H3_VISION_DEEPSTACK_INDEXES,
    H3_VISION_MAX_PIXELS,
    H3_VISION_MIN_PIXELS,
    IMAGE_PAD_TOKEN,
    VISION_END_TOKEN,
    VISION_NUM_HEADS,
    VISION_PATCH_SIZE,
    VISION_SPATIAL_MERGE_SIZE,
    VISION_START_TOKEN,
    VISION_TEMPORAL_PATCH_SIZE,
    preprocess_qwen3_vl_image,
)
from src.platform.runtime.native.text_encoders.tokenization import MiniMaxH3Tokenizer, Qwen3Tokenizer

from .._nvfp4_ref import default_tensor_scale, quantize_nvfp4


# --- config derivation: trimmed (50-layer) vs full (64-layer) --------------


def _h3_text_sd(num_layers: int, hidden: int = 5120) -> dict[str, torch.Tensor]:
    """Real H3 widths (hidden 5120, 64 heads/128 head_dim, 8 KV heads,
    intermediate 25600 — verified against `text_encoder/config.json` and the
    real checkpoint's shapes, ai/minimax_h3/te_bf16_header.json), tensors
    shrunk to a small fake vocab since only shapes matter to detection/config."""
    sd = {
        "model.embed_tokens.weight": torch.zeros(64, hidden),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(128),
        "model.layers.0.self_attn.k_norm.weight": torch.zeros(128),
        "model.layers.0.self_attn.q_proj.weight": torch.zeros(64 * 128, hidden),
        "model.layers.0.self_attn.k_proj.weight": torch.zeros(8 * 128, hidden),
        "model.layers.0.mlp.gate_proj.weight": torch.zeros(25600, hidden),
    }
    for i in range(num_layers):
        sd[f"model.layers.{i}.self_attn.q_norm.weight"] = torch.zeros(128)
    return sd


def test_config_derives_50_layers_from_the_trimmed_checkpoint():
    """The Comfy-Org repack carries EXACTLY layers 0..49 (50 layers) — not 51,
    correcting the plan's assumption (see the port report)."""
    sd = _h3_text_sd(num_layers=50)
    te_config = detect_te_config(sd)
    config = _build_config(te_config, sd)
    cfg = Qwen3Config.from_dict(config)
    assert cfg.num_hidden_layers == 50
    assert cfg.intermediate_size == 25600
    assert cfg.num_attention_heads == 64
    assert cfg.num_key_value_heads == 8
    assert cfg.head_dim == 128
    assert cfg.rope_theta == 5000000.0


def test_config_derives_64_layers_from_a_full_checkpoint():
    sd = _h3_text_sd(num_layers=64)
    te_config = detect_te_config(sd)
    config = _build_config(te_config, sd)
    cfg = Qwen3Config.from_dict(config)
    assert cfg.num_hidden_layers == 64


# --- hidden_states[50] tap index: bite-checkable off-by-one -----------------

_H3_TINY_CFG = {
    "hidden_size": 16, "num_layers": 50, "vocab_size": 151936,
    "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 8,
    "intermediate_size": 32, "rope_theta": 5000000.0,
}


def _tiny_h3_module() -> Qwen3Model:
    ops = pick_operations(torch.float32, torch.float32)
    m = Qwen3Model.from_config(_H3_TINY_CFG, ops)
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


def test_layer_index_constant_is_49():
    # hidden_states[50] in HF's embedding-inclusive numbering (hidden_states[0]
    # = embeddings) is the output of 0-indexed decoder layer 49.
    assert MINIMAX_H3_TEXT_ENCODER_LAYER_INDEX == 49


def test_tap_matches_manual_post_block_49_residual():
    """The off-by-one bite-check: the module's layer-49 output-capture must
    equal a hand-run forward stopped right after block 49 — not block 48
    (hidden_states[49]) or block 50 (hidden_states[51])."""
    m = _tiny_h3_module()
    ids = torch.tensor([[5, 6, 7]])
    x = m.model.embed_tokens(ids).float()
    cos, sin = m.model._rope(3, x.device, x.dtype)
    causal = torch.empty(3, 3, dtype=x.dtype).fill_(torch.finfo(x.dtype).min / 4).triu_(1)
    manual = None
    for i, layer in enumerate(m.model.layers):
        x = layer(x, cos, sin, causal)
        if i == MINIMAX_H3_TEXT_ENCODER_LAYER_INDEX:
            manual = x.clone()
            break
    assert manual is not None

    fwd = m(ids, attention_mask=None, layers_to_extract=(MINIMAX_H3_TEXT_ENCODER_LAYER_INDEX,), capture="output")
    assert torch.allclose(manual, fwd.squeeze(1), atol=1e-5)


def test_tap_is_sensitive_to_the_index_neighbors_differ():
    """Confirms the tap genuinely depends on the exact index — layers 48 and
    50 give a DIFFERENT result than 49, so an off-by-one is not silently
    unobservable."""
    m = _tiny_h3_module()
    ids = torch.tensor([[5, 6, 7]])
    fwd_49 = m(ids, attention_mask=None, layers_to_extract=(49,), capture="output")
    fwd_48 = m(ids, attention_mask=None, layers_to_extract=(48,), capture="output")
    assert not torch.allclose(fwd_49, fwd_48)


# --- post_load device lookup vs an int8-quantized embed_tokens --------------


def test_post_load_survives_an_int8_quantized_embed_tokens():
    """MiniMax-H3's real nvfp4_awq TE quantizes `model.embed_tokens` int8
    (verified against ai/minimax_h3/te_nvfp4_awq_header.json). The vendored
    int8 Embedding (vendor/gpl/comfyui/ops.py) clears `.weight` to `None`
    after loading its dequant state into other buffers — the SAME pattern
    Nvfp4Linear already used — so `recompute_inv_freq`'s old
    `self.embed_tokens.weight.device` read would AttributeError on
    `None.device` for this real checkpoint. `post_load()` must survive it and
    still produce a finite, correctly-placed `inv_freq`.
    """
    m = _tiny_h3_module()

    # Quantize embed_tokens in place, the exact recipe test_int8_embedding.py
    # uses to build the vendored int8 Embedding's on-disk state.
    num_emb, dim = m.cfg.vocab_size, m.cfg.hidden_size
    w = torch.randn(num_emb, dim) * 0.05
    row_absmax = w.abs().amax(dim=-1, keepdim=True).clamp(min=1e-12)
    scale = row_absmax / 127.0
    q = torch.round(w / scale).clamp(-127, 127).to(torch.int8)
    quant_blob = torch.tensor(
        list(json.dumps({"format": "int8_tensorwise"}).encode("utf-8")), dtype=torch.uint8,
    )
    sd = {"weight": q, "weight_scale": scale, "comfy_quant": quant_blob}
    m.model.embed_tokens._load_from_state_dict(dict(sd), "", {}, True, [], [], [])
    # Confirms the scenario is real (weight actually cleared), not a no-op.
    assert m.model.embed_tokens.weight is None
    assert m.model.embed_tokens._int8_weight is not None

    m.post_load()  # must not raise

    assert torch.isfinite(m.model.inv_freq).all()
    assert m.model.inv_freq.device == m.model.embed_tokens._int8_weight.device


def test_post_load_unquantized_path_unchanged():
    """Bystander check: the ordinary (unquantized) embed_tokens path — every
    existing text encoder — must land on the exact same device as before."""
    m = _tiny_h3_module()
    m.post_load()
    assert torch.isfinite(m.model.inv_freq).all()
    assert m.model.inv_freq.device == m.model.embed_tokens.weight.device


# --- has_final_norm: MiniMax-H3's real trimmed checkpoint has no `norm` ------


def test_has_final_norm_defaults_true():
    assert Qwen3Config(hidden_size=16, intermediate_size=32, num_hidden_layers=2, vocab_size=24).has_final_norm


def test_norm_not_constructed_when_has_final_norm_false():
    """The load-bearing structural fix: MiniMax-H3's real Comfy-Org trimmed
    repack has NO `model.norm.weight` key at all — constructing `self.norm`
    anyway makes the integrity gate demand a key the checkpoint can never
    supply (`NativeEngineLoadIntegrityError: ... missing ... model.norm.weight`,
    the exact failure the maintainer hit on GPU). Not constructing the
    module at all — not allowlisting it as expected-missing — is what makes
    the mismatch disappear on both sides."""
    ops = pick_operations(torch.float32, torch.float32)
    cfg = {**_H3_TINY_CFG, "has_final_norm": False}
    m = Qwen3Model.from_config(cfg, ops)
    assert not hasattr(m.model, "norm")
    assert "model.norm.weight" not in dict(m.state_dict())


def test_norm_still_constructed_by_default():
    """Bystander check: every OTHER checkpoint (Klein/Krea-2/Z-Image/Anima,
    and a hypothetical full 64-layer H3) is unaffected — `has_final_norm`
    defaults True and `norm` is constructed exactly as before this fix."""
    ops = pick_operations(torch.float32, torch.float32)
    m = Qwen3Model.from_config(_H3_TINY_CFG, ops)
    assert hasattr(m.model, "norm")
    assert "model.norm.weight" in dict(m.state_dict())


# --- full end-to-end dry run against the real nvfp4_awq key/dtype pattern ---


def _quantize_nvfp4_inplace(sd: dict, key: str, *, pre_quant_scale: bool) -> None:
    """Real key pattern (ai/minimax_h3/te_nvfp4_awq_header.json): `weight`
    (nibble-packed U8) + `weight_scale` (swizzled F8_E4M3) + `weight_scale_2`
    (F32 scalar) + `comfy_quant`; `mlp.down_proj`/`self_attn.o_proj` ONLY
    additionally carry a BF16 `pre_quant_scale` (AWQ activation smoothing)."""
    w = sd[key].to(torch.float32)
    out_f, in_f = w.shape
    tensor_scale = default_tensor_scale(w)
    packed, block_scale, _codes, _block = quantize_nvfp4(w, tensor_scale)
    prefix = key.removesuffix("weight")
    sd[key] = packed
    sd[f"{prefix}weight_scale"] = block_scale
    sd[f"{prefix}weight_scale_2"] = tensor_scale.clone()
    sd[f"{prefix}comfy_quant"] = torch.tensor(
        list(json.dumps({"format": "nvfp4", "full_precision_matrix_mult": True}).encode("utf-8")), dtype=torch.uint8,
    )
    if pre_quant_scale:
        sd[f"{prefix}pre_quant_scale"] = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75


def _quantize_int8_embedding_inplace(sd: dict, key: str) -> None:
    """Real key pattern: `model.embed_tokens` is int8_tensorwise — I8
    `weight` + per-row F32 `weight_scale` + `comfy_quant` — NOT nvfp4-packed
    (embeddings are quantized differently from the attention/MLP linears)."""
    w = sd[key].to(torch.float32)
    row_absmax = w.abs().amax(dim=-1, keepdim=True).clamp(min=1e-12)
    scale = row_absmax / 127.0
    q = torch.round(w / scale).clamp(-127, 127).to(torch.int8)
    prefix = key.removesuffix("weight")
    sd[key] = q
    sd[f"{prefix}weight_scale"] = scale
    sd[f"{prefix}comfy_quant"] = torch.tensor(
        list(json.dumps({"format": "int8_tensorwise"}).encode("utf-8")), dtype=torch.uint8,
    )


def _h3_dry_run_cfg() -> dict:
    """A ROUND-TRIPPABLE (build -> save -> re-detect -> rebuild) skeleton
    config — unlike `_H3_TINY_CFG` (hidden=16, used by tests that build a
    module directly and never re-detect it), this one must satisfy detection's
    OWN discriminators or the reload routes to the wrong variant entirely:
    `hidden_size` must be >= 5120 (te_detect.py's qwen3vl_32b width branch),
    and the vision constants `_build_config` HARDCODES on reload
    (`vision_num_heads`/`vision_patch_size`/`vision_temporal_patch_size`/
    `vision_spatial_merge_size`, plus `vision_deepstack_indexes` for this
    variant specifically) must match here too, or a round-trip
    save+reload shape-mismatches. Everything else (layer count, intermediate
    size, vocab, vision widths, position embeddings) IS shape-derived on
    reload, so those stay tiny for speed.
    """
    return {
        "hidden_size": 5120, "num_layers": 2, "vocab_size": 24,
        "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 16,
        "intermediate_size": 32, "rope_theta": 5000000.0,
        "vision": True, "vision_top_level": True,
        "vision_hidden_size": 32, "vision_intermediate_size": 16,
        "vision_num_layers": 2, "vision_num_heads": VISION_NUM_HEADS,
        "vision_patch_size": VISION_PATCH_SIZE,
        "vision_temporal_patch_size": VISION_TEMPORAL_PATCH_SIZE,
        "vision_spatial_merge_size": VISION_SPATIAL_MERGE_SIZE,
        "vision_num_position_embeddings": 16,
        "vision_deepstack_indexes": H3_VISION_DEEPSTACK_INDEXES,
    }


def _build_h3_trimmed_nvfp4_awq_sd() -> dict[str, torch.Tensor]:
    """A tiny but STRUCTURALLY FAITHFUL replica of the real nvfp4_awq
    checkpoint's key pattern (ai/minimax_h3/te_nvfp4_awq_header.json): no
    `model.norm.weight`/`lm_head.*` at all, `model.embed_tokens` int8, every
    attention/MLP linear in every layer nvfp4 (`o_proj`/`down_proj` also
    carrying `pre_quant_scale`), the vision tower top-level (`visual.*`) and
    UNQUANTIZED (matches the real header: only the LM's linears + embedding
    are quantized, the tower stays BF16), norms (RMSNorm/LayerNorm) left
    unquantized throughout (never quantized in the real checkpoint either).
    Real widths (5120/50-layer/151936-vocab) would be multi-GB to build here
    — shrunk to tiny dims that preserve every divisibility constraint nvfp4's
    16-wide blocks need (`_nvfp4_ref.py`'s `_NVFP4_BLOCK`), same convention
    tests/platform/runtime/native/text_encoders/test_loader_awq_sidecars.py
    already uses for the key-gate-only check this test goes further than.
    """
    ops = pick_operations(torch.float32, torch.float32)
    # has_final_norm defaults True here so the skeleton HAS a `norm` key to
    # steal the name from; it is then deleted below to simulate the real
    # trimmed checkpoint's genuine absence — detection re-derives False on
    # its own from that absence when the crafted sd is loaded back.
    cfg = _h3_dry_run_cfg()
    m = Qwen3Model.from_config(cfg, ops)
    torch.manual_seed(0)
    sd: dict[str, torch.Tensor] = {}
    for k, v in m.state_dict().items():
        sd[k] = torch.ones_like(v) if k.endswith(("norm.weight", "norm.bias")) else torch.randn_like(v) * 0.05
    del sd["model.norm.weight"]

    for layer in range(cfg["num_layers"]):
        prefix = f"model.layers.{layer}.self_attn."
        for proj in ("q_proj", "k_proj", "v_proj"):
            _quantize_nvfp4_inplace(sd, f"{prefix}{proj}.weight", pre_quant_scale=False)
        _quantize_nvfp4_inplace(sd, f"{prefix}o_proj.weight", pre_quant_scale=True)
        prefix = f"model.layers.{layer}.mlp."
        for proj in ("gate_proj", "up_proj"):
            _quantize_nvfp4_inplace(sd, f"{prefix}{proj}.weight", pre_quant_scale=False)
        _quantize_nvfp4_inplace(sd, f"{prefix}down_proj.weight", pre_quant_scale=True)
    _quantize_int8_embedding_inplace(sd, "model.embed_tokens.weight")
    return sd


def test_h3_trimmed_nvfp4_awq_checkpoint_loads_end_to_end():
    """The dry run this bug needed: drives the REAL `load_text_encoder` path
    (detect -> build config -> construct -> integrity-load -> post_load),
    not just the key-integrity gate in isolation, against a checkpoint whose
    key PATTERN matches the real nvfp4_awq repack exactly (missing norm,
    int8 embedding, nvfp4 linears with selective AWQ smoothing, top-level
    unquantized vision tower). Catches the missing-`model.norm.weight`
    integrity error (and any future regression in this same chain) on CPU,
    in milliseconds, without the real 14.6 GB file.
    """
    pytest.importorskip("transformers")
    sd = _build_h3_trimmed_nvfp4_awq_sd()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "qwen3vl_32b_minimax_h3_nvfp4_awq_tiny.safetensors")
        save_file(sd, path)
        enc = load_text_encoder(path, device="cpu", vision=True)

    assert isinstance(enc, MiniMaxH3TextEncoder)
    assert hasattr(enc.module, "visual")           # vision tower loaded, top-level
    assert not hasattr(enc.module.model, "norm")   # the fix: no norm constructed
    assert torch.isfinite(enc.module.model.inv_freq).all()


# --- vision-tower attachment point: top-level (32B) vs nested (4B) ---------

_H3_VISION_CFG = {
    "vision_hidden_size": 8, "vision_intermediate_size": 16,
    "vision_num_layers": 2, "vision_num_heads": 2,
    "vision_patch_size": 2, "vision_temporal_patch_size": 2,
    "vision_spatial_merge_size": 2, "vision_num_position_embeddings": 16,
    "vision_deepstack_indexes": (0,),
}
_H3_VL_CFG = {**_H3_TINY_CFG, "vision": True, "vision_top_level": True, **_H3_VISION_CFG}


def _tiny_h3_vl_module() -> Qwen3Model:
    ops = pick_operations(torch.float32, torch.float32)
    m = Qwen3Model.from_config(_H3_VL_CFG, ops)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02)
    for mod in m.modules():
        if isinstance(mod, torch.nn.LayerNorm):
            torch.nn.init.ones_(mod.weight)
            torch.nn.init.zeros_(mod.bias)
    m.post_load()
    m.eval()
    return m


def test_vision_top_level_attaches_on_qwen3model_not_nested():
    m = _tiny_h3_vl_module()
    assert hasattr(m, "visual")
    assert not hasattr(m.model, "visual")
    keys = set(m.state_dict())
    assert any(k.startswith("visual.") and not k.startswith("model.visual.") for k in keys)


def test_vision_nested_default_still_attaches_under_model():
    """Bystander check: the pre-existing Krea-2 (4B) nested attachment is
    UNCHANGED by the top-level branch added for H3."""
    cfg = {**_H3_TINY_CFG, "vision": True, "vision_top_level": False, **_H3_VISION_CFG}
    ops = pick_operations(torch.float32, torch.float32)
    m = Qwen3Model.from_config(cfg, ops)
    assert hasattr(m.model, "visual")
    assert not hasattr(m, "visual")


# --- _find_pad_run ------------------------------------------------------


def test_find_pad_run_locates_multiple_runs_in_order():
    ids = [1, 2, 99, 99, 99, 3, 99, 99, 4]
    start1, len1 = _find_pad_run(ids, 99, 0)
    assert (start1, len1) == (2, 3)
    start2, len2 = _find_pad_run(ids, 99, start1 + len1)
    assert (start2, len2) == (6, 2)


def test_find_pad_run_raises_when_absent():
    with pytest.raises(ValueError):
        _find_pad_run([1, 2, 3], 99, 0)


# --- tokenizer primitives + hand-built presentation -------------------------


def test_minimax_h3_tokenizer_has_no_special_tokens():
    pytest.importorskip("transformers")
    tok = MiniMaxH3Tokenizer()
    ids = tok("a cat")
    assert tok._tok.decode(ids) == "a cat"


def test_minimax_h3_tokenizer_shares_vocab_with_bundled_qwen3_tokenizer():
    """Deliverable #5 verdict: no new tokenizer assets — same bundled asset,
    same vocab (verified: `text_encoder/config.json` vocab_size 151936 ==
    both the bundled asset's and the real checkpoint's `embed_tokens` row
    count; same `tokenizer_class`, same 26 added special tokens)."""
    pytest.importorskip("transformers")
    h3 = MiniMaxH3Tokenizer()
    plain = Qwen3Tokenizer()
    assert h3("hello world") == plain._tok("hello world", add_special_tokens=False)["input_ids"]
    assert h3._tok.vocab_size == plain._tok.vocab_size


def test_build_fl2va_presentation_token_sequence_matches_hand_built():
    """Exact token id sequence for a prompt + 1 image, hand-built per
    encoders.py's `MiniMaxH3FL2VATextEncoderStep` (Apache-2.0): a
    `"<Picture i>: "` label + `<|vision_start|>` + n*`<|image_pad|>` +
    `<|vision_end|>`, then the prompt verbatim. Exercises the tokenizer
    primitives `encode_presentation` relies on — presentation-BUILDING is a
    later pipe stage, this only proves the primitives it needs are correct.
    """
    pytest.importorskip("transformers")
    tok = MiniMaxH3Tokenizer()
    num_image_tokens = 4

    label_ids = tok("<Picture 1>: ")
    vision_ids = (
        [tok.convert_tokens_to_ids("<|vision_start|>")]
        + [tok.convert_tokens_to_ids("<|image_pad|>")] * num_image_tokens
        + [tok.convert_tokens_to_ids("<|vision_end|>")]
    )
    prompt_ids = tok("a red car")
    token_ids = label_ids + vision_ids + prompt_ids

    assert vision_ids[0] == VISION_START_TOKEN
    assert vision_ids[-1] == VISION_END_TOKEN
    assert vision_ids[1:-1] == [IMAGE_PAD_TOKEN] * num_image_tokens
    assert token_ids == label_ids + vision_ids + prompt_ids  # exact hand-built sequence

    span_start, span_len = _find_pad_run(token_ids, IMAGE_PAD_TOKEN, 0)
    assert span_start == len(label_ids) + 1  # after the label + <|vision_start|>
    assert span_len == num_image_tokens


# --- encode contract: text-only ---------------------------------------------


def test_encode_text_only_matches_direct_layer_49_capture():
    pytest.importorskip("transformers")
    m = _tiny_h3_module()
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    out = enc.encode(["a cat"])
    assert set(out) == {"context"}
    assert out["context"].shape[0] == 1
    assert out["context"].shape[2] == _H3_TINY_CFG["hidden_size"]

    token_ids = MiniMaxH3Tokenizer()("a cat")
    ids = torch.tensor([token_ids])
    expected = m(
        ids, attention_mask=None, layers_to_extract=(MINIMAX_H3_TEXT_ENCODER_LAYER_INDEX,), capture="output"
    ).squeeze(1)
    assert torch.allclose(out["context"], expected)


def test_encode_rejects_multi_prompt_batch():
    pytest.importorskip("transformers")
    m = _tiny_h3_module()
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    with pytest.raises(ValueError, match="one prompt"):
        enc.encode(["a", "b"])


def test_encode_no_chat_template_no_bos_eos():
    """H3's contract is raw prompt, add_special_tokens=False, no template —
    the tokenized presentation must be exactly the prompt's own tokens."""
    pytest.importorskip("transformers")
    tok = MiniMaxH3Tokenizer()
    assert tok("a cat") == tok._tok("a cat", add_special_tokens=False)["input_ids"]


# --- encode contract: vision-conditioned (fl2va-shaped) ---------------------


def test_encode_presentation_without_vision_tower_raises():
    m = _tiny_h3_module()  # no vision tower loaded
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    run = MiniMaxH3VisionRun(patches=torch.rand(4, 12), grid_thw=torch.tensor([[1, 2, 2]]), pad_token_id=IMAGE_PAD_TOKEN)
    with pytest.raises(NativeEngineUnsupportedError):
        enc.encode_presentation([1, 2, 3], vision_runs=[run])


def test_encode_presentation_with_one_vision_run_shape_and_finite():
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")

    label_ids = tok("<Picture 1>: ")
    img = torch.rand(32, 32, 3)
    visual = m.visual
    patches, grid_thw = preprocess_qwen3_vl_image(
        img, grounding_px=0, patch_size=visual.patch_size,
        temporal_patch_size=visual.patch_embed.temporal_patch_size,
        merge_size=visual.spatial_merge_size,
    )
    merged_count = int((grid_thw[0, 1] * grid_thw[0, 2]) // (visual.spatial_merge_size ** 2))
    vision_ids = (
        [tok.convert_tokens_to_ids("<|vision_start|>")]
        + [IMAGE_PAD_TOKEN] * merged_count
        + [tok.convert_tokens_to_ids("<|vision_end|>")]
    )
    prompt_ids = tok("a red car")
    token_ids = label_ids + vision_ids + prompt_ids
    run = MiniMaxH3VisionRun(patches=patches, grid_thw=grid_thw, pad_token_id=IMAGE_PAD_TOKEN)

    out = enc.encode_presentation(token_ids, vision_runs=[run])
    assert set(out) == {"context"}
    assert out["context"].shape == (1, len(token_ids), _H3_TINY_CFG["hidden_size"])
    assert torch.isfinite(out["context"]).all()


def test_encode_presentation_splice_length_mismatch_raises():
    """The placeholder run's token count must equal the vision tower's own
    merged-token count for that block, or the caller built a mismatched
    presentation (wrong number of `<|image_pad|>` tokens for the image)."""
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")

    img = torch.rand(32, 32, 3)
    visual = m.visual
    patches, grid_thw = preprocess_qwen3_vl_image(
        img, grounding_px=0, patch_size=visual.patch_size,
        temporal_patch_size=visual.patch_embed.temporal_patch_size,
        merge_size=visual.spatial_merge_size,
    )
    # One <|image_pad|> too few — deliberately wrong.
    merged_count = int((grid_thw[0, 1] * grid_thw[0, 2]) // (visual.spatial_merge_size ** 2))
    vision_ids = (
        [tok.convert_tokens_to_ids("<|vision_start|>")]
        + [IMAGE_PAD_TOKEN] * (merged_count - 1)
        + [tok.convert_tokens_to_ids("<|vision_end|>")]
    )
    token_ids = vision_ids + tok("prompt")
    run = MiniMaxH3VisionRun(patches=patches, grid_thw=grid_thw, pad_token_id=IMAGE_PAD_TOKEN)

    with pytest.raises(ValueError, match="merged token"):
        enc.encode_presentation(token_ids, vision_runs=[run])


# --- encode_request: the pipes-layer contract (builds the fl2va presentation) ---


def test_encode_request_text_only_all_tags_are_text():
    pytest.importorskip("transformers")
    m = _tiny_h3_module()
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")

    out = enc.encode_request("a cat on a red car")
    assert set(out) == {"context", "token_tags"}
    token_ids = MiniMaxH3Tokenizer()("a cat on a red car")
    assert out["token_tags"].shape == (len(token_ids),)
    assert out["context"].shape == (1, len(token_ids), _H3_TINY_CFG["hidden_size"])
    assert torch.equal(out["token_tags"], torch.full((len(token_ids),), MINIMAX_H3_TEXT_TAG, dtype=torch.long))


def test_encode_request_empty_images_list_is_treated_as_no_images():
    """Matches the pipes adapter's `images=[images] if images else None` —
    an empty list must take the plain t2va path, not the vision path."""
    pytest.importorskip("transformers")
    m = _tiny_h3_module()  # no vision tower loaded
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    out = enc.encode_request("a cat", images=[])
    assert set(out) == {"context", "token_tags"}


def test_encode_request_without_vision_tower_raises_when_images_given():
    m = _tiny_h3_module()  # no vision tower loaded
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    with pytest.raises(NativeEngineUnsupportedError):
        enc.encode_request("a cat", images=[torch.rand(32, 32, 3)])


def test_encode_request_with_one_image_vision_block_tagged_video_exact_boundaries():
    """The load-bearing derivation this task exists to verify: the `"<Picture
    1>: "` LABEL is tagged TEXT (1), and ONLY the vision markup block
    (`<|vision_start|>` + image_pad*N + `<|vision_end|>`) is tagged VIDEO (0)
    — not the label too. Boundaries must be EXACT (off by one row here would
    silently mis-tag a real token during generation)."""
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    img = torch.rand(32, 32, 3)

    # Hand-derive the expected boundaries the same way `encode_request` does
    # internally, using only the PUBLIC tokenizer/preprocessing primitives —
    # never reading `encode_request`'s own intermediate state. Must use H3's
    # OWN pixel-area bounds (encode_request's default), not
    # preprocess_qwen3_vl_image's own (Krea-2-shaped) defaults.
    label_ids = tok("<Picture 1>: ")
    visual = m.visual
    _patches, grid_thw = preprocess_qwen3_vl_image(
        img, grounding_px=0, min_pixels=H3_VISION_MIN_PIXELS, max_pixels=H3_VISION_MAX_PIXELS,
        patch_size=visual.patch_size,
        temporal_patch_size=visual.patch_embed.temporal_patch_size,
        merge_size=visual.spatial_merge_size,
    )
    num_image_tokens = int(grid_thw[0].prod()) // (visual.spatial_merge_size ** 2)
    vision_block_len = 1 + num_image_tokens + 1  # vision_start + pads + vision_end
    prompt_ids = tok("a red car")
    expected_len = len(label_ids) + vision_block_len + len(prompt_ids)

    out = enc.encode_request("a red car", images=[img])
    tags = out["token_tags"]
    assert tags.shape == (expected_len,)
    assert out["context"].shape == (1, expected_len, _H3_TINY_CFG["hidden_size"])

    label_span = slice(0, len(label_ids))
    vision_span = slice(len(label_ids), len(label_ids) + vision_block_len)
    prompt_span = slice(len(label_ids) + vision_block_len, expected_len)
    assert torch.equal(tags[label_span], torch.full((len(label_ids),), MINIMAX_H3_TEXT_TAG, dtype=torch.long))
    assert torch.equal(tags[vision_span], torch.full((vision_block_len,), MINIMAX_H3_VIDEO_TAG, dtype=torch.long))
    assert torch.equal(tags[prompt_span], torch.full((len(prompt_ids),), MINIMAX_H3_TEXT_TAG, dtype=torch.long))
    # The exact boundary values, independent of the slicing arithmetic above.
    assert tags[len(label_ids) - 1].item() == MINIMAX_H3_TEXT_TAG   # last label token
    assert tags[len(label_ids)].item() == MINIMAX_H3_VIDEO_TAG      # first vision-block token (<|vision_start|>)
    assert tags[len(label_ids) + vision_block_len - 1].item() == MINIMAX_H3_VIDEO_TAG  # last (<|vision_end|>)
    assert tags[len(label_ids) + vision_block_len].item() == MINIMAX_H3_TEXT_TAG        # first prompt token


# --- H3's smart-resize pixel-area bounds vs Krea-2's (min_pixels/max_pixels) ---


def test_h3_bounds_constants_are_not_krea2_defaults():
    """Sanity: the whole point of parameterizing is that these differ from
    `preprocess_qwen3_vl_image`'s own (Krea-2-shaped) defaults."""
    assert H3_VISION_MIN_PIXELS == 65536
    assert H3_VISION_MAX_PIXELS == 16777216
    assert H3_VISION_MIN_PIXELS != 3136          # preprocess_qwen3_vl_image's default min_pixels
    assert H3_VISION_MAX_PIXELS != 12845056      # preprocess_qwen3_vl_image's default max_pixels


def test_h3_bounds_force_an_upscale_that_krea2_defaults_do_not():
    """Proof the parameter is LIVE, not just present: a 224x224 image (area
    50176) sits above Krea-2's default min_pixels (3136, no upscale) but
    below H3's own min_pixels (65536, forces an upscale) -- so the same
    image must resize DIFFERENTLY under the two bound sets."""
    img = torch.rand(224, 224, 3)

    _patches, grid_default = preprocess_qwen3_vl_image(img, grounding_px=0)
    _patches, grid_h3 = preprocess_qwen3_vl_image(
        img, grounding_px=0, min_pixels=H3_VISION_MIN_PIXELS, max_pixels=H3_VISION_MAX_PIXELS,
    )

    assert grid_default.tolist() == [[1, 14, 14]]   # 224x224 unchanged: 50176 > 3136
    assert grid_h3.tolist() == [[1, 16, 16]]         # upscaled to 256x256: 50176 < 65536
    assert grid_default.tolist() != grid_h3.tolist()


def test_encode_request_defaults_to_h3_bounds_not_krea2_defaults():
    """`encode_request` must pass H3's bounds through to preprocessing by
    DEFAULT (a caller that never overrides min_pixels/max_pixels still gets
    the correct H3 behavior, not Krea-2's smaller bounds)."""
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    img = torch.rand(224, 224, 3)

    out = enc.encode_request("a red car", images=[img])
    label_ids = tok("<Picture 1>: ")
    prompt_ids = tok("a red car")
    visual = m.visual
    _patches, grid_h3 = preprocess_qwen3_vl_image(
        img, grounding_px=0, min_pixels=H3_VISION_MIN_PIXELS, max_pixels=H3_VISION_MAX_PIXELS,
        patch_size=visual.patch_size, temporal_patch_size=visual.patch_embed.temporal_patch_size,
        merge_size=visual.spatial_merge_size,
    )
    num_image_tokens = int(grid_h3[0].prod()) // (visual.spatial_merge_size ** 2)
    expected_len = len(label_ids) + 1 + num_image_tokens + 1 + len(prompt_ids)
    assert out["context"].shape[1] == expected_len
    assert out["token_tags"].shape[0] == expected_len


def test_encode_request_context_and_tags_shape_agreement_multi_image():
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    imgs = [torch.rand(32, 32, 3), torch.rand(16, 16, 3)]

    out = enc.encode_request("blend them", images=imgs)
    assert out["context"].shape[1] == out["token_tags"].shape[0]
    assert torch.isfinite(out["context"]).all()
    # Two labels + two vision blocks must each contribute a VIDEO run — never
    # zero video-tagged rows for a two-image request.
    assert (out["token_tags"] == MINIMAX_H3_VIDEO_TAG).any()
    assert (out["token_tags"] == MINIMAX_H3_TEXT_TAG).any()


# --- encode_reference_request: the ref2va pipes-layer contract --------------


def test_encode_reference_request_text_only_matches_encode_request():
    """No references given must be the exact same t2va presentation as
    `encode_request` -- the two only diverge once there is a reference to
    label."""
    pytest.importorskip("transformers")
    m = _tiny_h3_module()
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")

    out = enc.encode_reference_request("a cat on a red car", [])
    assert set(out) == {"context", "token_tags"}
    token_ids = MiniMaxH3Tokenizer()("a cat on a red car")
    assert out["token_tags"].shape == (len(token_ids),)
    assert torch.equal(out["token_tags"], torch.full((len(token_ids),), MINIMAX_H3_TEXT_TAG, dtype=torch.long))


def test_encode_reference_request_rejects_an_unknown_reference_kind():
    m = _tiny_h3_module()
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    with pytest.raises(ValueError, match="'image', 'video' or 'audio'"):
        enc.encode_reference_request("a cat", [MiniMaxH3Reference(kind="sound", media=None)])


def test_encode_reference_request_without_vision_tower_raises_when_images_given():
    m = _tiny_h3_module()  # no vision tower loaded
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    with pytest.raises(NativeEngineUnsupportedError):
        enc.encode_reference_request("a cat", [MiniMaxH3Reference(kind="image", media=torch.rand(32, 32, 3))])


def test_encode_reference_request_numbers_labels_per_modality_not_per_index():
    """The labels are the ENCODER's to derive, not the caller's: three
    independent counters advance in packed order, so a video sitting between
    two images does not consume a `"<Picture i>"` number, and vice versa.
    A flat per-index numbering would label these 1, 2, 3, 4."""
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    seen: dict = {}
    inner = enc.encode_presentation
    enc.encode_presentation = lambda token_ids, vision_runs=None: (
        seen.setdefault("token_ids", token_ids), inner(token_ids, vision_runs=vision_runs),
    )[1]

    enc.encode_reference_request("blend", [
        MiniMaxH3Reference(kind="image", media=torch.rand(32, 32, 3)),
        MiniMaxH3Reference(kind="video", media=_video(49)),
        MiniMaxH3Reference(kind="image", media=torch.rand(32, 32, 3)),
        MiniMaxH3Reference(kind="audio", has_audio=True),
    ])
    ids = seen["token_ids"]

    def contains(label: str) -> bool:
        want = tok(label)
        return any(ids[i:i + len(want)] == want for i in range(len(ids) - len(want) + 1))

    assert contains("<Picture 1>: ")
    assert contains("<Video 1>: ")
    assert contains("<Picture 2>: ")   # NOT "<Picture 3>": the video has its own counter
    assert contains("<Audio 1>: ")
    assert not contains("<Picture 3>: ")
    assert not contains("<Video 2>: ")


def test_an_audio_bearing_video_is_labelled_audio_before_video():
    """MiniMax-H3 emits `"<Audio j>: "` BEFORE `"<Video k>: "` for a video
    that carries sound, mirroring the order its rows are packed in -- and as
    two separate tokenizer calls, which is not the same token sequence as one
    concatenated string at the BPE seam."""
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    seen: dict = {}
    inner = enc.encode_presentation
    enc.encode_presentation = lambda token_ids, vision_runs=None: (
        seen.setdefault("token_ids", token_ids), inner(token_ids, vision_runs=vision_runs),
    )[1]

    enc.encode_reference_request(
        "hum", [MiniMaxH3Reference(kind="video", media=_video(49), has_audio=True)],
    )

    two_calls = tok("<Audio 1>: ") + tok("<Video 1>: ")
    assert seen["token_ids"][: len(two_calls)] == two_calls
    assert seen["token_ids"][: len(two_calls)] != tok("<Audio 1>: <Video 1>: ")


def test_encode_reference_request_numbered_labels_match_encode_request_for_images_only():
    """When the caller passes the SAME `"<Picture i>: "` numbering
    `encode_request` hardcodes (the generator pipe's own image-only ref2va
    labeling), the two entry points must produce byte-identical output --
    the underlying presentation math for an image-only reference list is the
    same either way (diffusers' ref2va and fl2va per-modality counters agree
    when every reference is an image)."""
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    imgs = [torch.rand(32, 32, 3), torch.rand(16, 16, 3)]
    references = [MiniMaxH3Reference(kind="image", media=img) for img in imgs]

    via_request = enc.encode_request("blend them", images=imgs)
    via_reference_request = enc.encode_reference_request("blend them", references)

    torch.testing.assert_close(via_request["context"], via_reference_request["context"], rtol=0, atol=0)
    assert torch.equal(via_request["token_tags"], via_reference_request["token_tags"])


def test_encode_reference_request_context_and_tags_shape_agreement_multi_image():
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    imgs = [torch.rand(32, 32, 3), torch.rand(16, 16, 3)]
    references = [MiniMaxH3Reference(kind="image", media=img) for img in imgs]

    out = enc.encode_reference_request("blend them", references)
    assert out["context"].shape[1] == out["token_tags"].shape[0]
    assert torch.isfinite(out["context"]).all()
    assert (out["token_tags"] == MINIMAX_H3_VIDEO_TAG).any()
    assert (out["token_tags"] == MINIMAX_H3_TEXT_TAG).any()


# --- encode_reference_request: video and audio references -------------------
#
# A ref2va request mixes modalities and every one of them presents
# differently: an image is one `<|image_pad|>` block, a video is one
# `<|video_pad|>` block per merged frame PAIR with its own `"<t seconds>"`
# text between them, and audio is a label with no vision block at all.

from src.platform.runtime.native.text_encoders.qwen3 import (  # noqa: E402
    MINIMAX_H3_VIDEO_FPS,
    MINIMAX_H3_VIDEO_SAMPLE_FPS,
    _sample_reference_video_frames,
)
from src.platform.runtime.native.text_encoders.qwen3_vl_vision import (  # noqa: E402
    VIDEO_PAD_TOKEN,
    preprocess_qwen3_vl_video,
)


def _video(num_frames: int, size: int = 32) -> torch.Tensor:
    return torch.rand(num_frames, size, size, 3)


# -- frame sampling and timestamp labelling ----------------------------------


def test_reference_video_is_read_at_two_frames_per_second():
    # 24 fps read at 2 fps -> stride 12: frames 0, 12, 24, ... only.
    indices, _timestamps = _sample_reference_video_frames(
        49, fps=MINIMAX_H3_VIDEO_FPS, sample_fps=MINIMAX_H3_VIDEO_SAMPLE_FPS, temporal_patch=2,
    )
    assert indices == [0, 12, 24, 36, 48]


def test_reference_video_block_timestamps_round_half_to_even():
    # The mean of a 2 fps pair is 0.25 s, and "{:.1f}" is round-half-to-EVEN,
    # so the first block renders "<0.2 seconds>" -- NOT "<0.3 seconds>".
    _indices, timestamps = _sample_reference_video_frames(
        49, fps=MINIMAX_H3_VIDEO_FPS, sample_fps=MINIMAX_H3_VIDEO_SAMPLE_FPS, temporal_patch=2,
    )
    assert timestamps == [0.25, 1.25, 2.0]
    assert f"<{timestamps[0]:.1f} seconds>" == "<0.2 seconds>"


def test_reference_video_timestamp_list_is_padded_by_repeating_the_last():
    # 5 sampled frames merge into 3 pairs; the odd last frame is paired with
    # itself, so its block's timestamp is that frame's own timestamp.
    _indices, timestamps = _sample_reference_video_frames(
        49, fps=MINIMAX_H3_VIDEO_FPS, sample_fps=MINIMAX_H3_VIDEO_SAMPLE_FPS, temporal_patch=2,
    )
    assert len(timestamps) == 3
    assert timestamps[-1] == 2.0  # frame index 4 at 2 fps, paired with itself


def test_reference_video_shorter_than_one_merge_group_is_rejected():
    with pytest.raises(ValueError, match="at least 13 frames"):
        _sample_reference_video_frames(
            12, fps=MINIMAX_H3_VIDEO_FPS, sample_fps=MINIMAX_H3_VIDEO_SAMPLE_FPS, temporal_patch=2,
        )


# -- video patchification ----------------------------------------------------


def test_video_preprocess_merges_consecutive_frames_not_a_frame_with_itself():
    # grid_t must be F // temporal_patch, i.e. DISTINCT frames paired. The
    # image path's grid_t is always 1 (it repeats its single frame), so a
    # video wrongly routed through it would report grid_t == 1 here.
    patches, grid_thw = preprocess_qwen3_vl_video(
        _video(4, size=64), min_pixels=H3_VISION_MIN_PIXELS, max_pixels=H3_VISION_MAX_PIXELS,
        patch_size=VISION_PATCH_SIZE, temporal_patch_size=VISION_TEMPORAL_PATCH_SIZE,
        merge_size=VISION_SPATIAL_MERGE_SIZE,
    )
    grid_t, grid_h, grid_w = (int(v) for v in grid_thw[0])
    assert grid_t == 2
    assert patches.shape[0] == grid_t * grid_h * grid_w
    assert patches.shape[1] == 3 * VISION_TEMPORAL_PATCH_SIZE * VISION_PATCH_SIZE ** 2


def test_video_preprocess_pads_an_odd_frame_count_by_repeating_the_last():
    patches, grid_thw = preprocess_qwen3_vl_video(
        _video(5, size=64), min_pixels=H3_VISION_MIN_PIXELS, max_pixels=H3_VISION_MAX_PIXELS,
        patch_size=VISION_PATCH_SIZE, temporal_patch_size=VISION_TEMPORAL_PATCH_SIZE,
        merge_size=VISION_SPATIAL_MERGE_SIZE,
    )
    assert int(grid_thw[0, 0]) == 3
    assert patches.shape[0] == 3 * int(grid_thw[0, 1]) * int(grid_thw[0, 2])


def test_video_preprocess_resolves_one_spatial_grid_for_the_whole_stack():
    # Every frame shares the stack's own smart-resize grid -- the spatial part
    # of grid_thw must match what the image path resolves for one frame of it.
    frames = _video(4, size=64)
    _p, video_grid = preprocess_qwen3_vl_video(
        frames, min_pixels=H3_VISION_MIN_PIXELS, max_pixels=H3_VISION_MAX_PIXELS,
        patch_size=VISION_PATCH_SIZE, temporal_patch_size=VISION_TEMPORAL_PATCH_SIZE,
        merge_size=VISION_SPATIAL_MERGE_SIZE,
    )
    _p2, image_grid = preprocess_qwen3_vl_image(
        frames[0], grounding_px=0, min_pixels=H3_VISION_MIN_PIXELS, max_pixels=H3_VISION_MAX_PIXELS,
        patch_size=VISION_PATCH_SIZE, temporal_patch_size=VISION_TEMPORAL_PATCH_SIZE,
        merge_size=VISION_SPATIAL_MERGE_SIZE,
    )
    assert video_grid[0, 1:].tolist() == image_grid[0, 1:].tolist()


# -- the presentation --------------------------------------------------------


def _grid_for(module, video: torch.Tensor):
    visual = module.visual
    indices, timestamps = _sample_reference_video_frames(
        video.shape[0], fps=MINIMAX_H3_VIDEO_FPS, sample_fps=MINIMAX_H3_VIDEO_SAMPLE_FPS,
        temporal_patch=visual.patch_embed.temporal_patch_size,
    )
    _patches, grid_thw = preprocess_qwen3_vl_video(
        video[indices], min_pixels=H3_VISION_MIN_PIXELS, max_pixels=H3_VISION_MAX_PIXELS,
        patch_size=visual.patch_size, temporal_patch_size=visual.patch_embed.temporal_patch_size,
        merge_size=visual.spatial_merge_size,
    )
    per_block = int(grid_thw[0, 1]) * int(grid_thw[0, 2]) // (visual.spatial_merge_size ** 2)
    return timestamps, per_block


def test_video_reference_emits_one_timestamped_vision_block_per_frame_group():
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    video = _video(49)
    timestamps, per_block = _grid_for(m, video)

    out = enc.encode_reference_request("a drifting camera", [MiniMaxH3Reference(kind="video", media=video)])

    expected_len = len(tok("<Video 1>: "))
    for timestamp in timestamps:
        expected_len += len(tok(f"<{timestamp:.1f} seconds>")) + 1 + per_block + 1
    expected_len += len(tok("a drifting camera"))
    assert out["context"].shape == (1, expected_len, _H3_TINY_CFG["hidden_size"])
    assert out["token_tags"].shape == (expected_len,)
    assert torch.isfinite(out["context"]).all()
    # More than one vision block: three merged pairs, not one fused block.
    assert len(timestamps) == 3


def test_video_reference_uses_the_video_pad_token_not_the_image_pad_token():
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    seen: dict = {}
    inner = enc.encode_presentation

    def spy(token_ids, vision_runs=None):
        seen["token_ids"] = token_ids
        seen["runs"] = vision_runs
        return inner(token_ids, vision_runs=vision_runs)

    enc.encode_presentation = spy
    enc.encode_reference_request("a drifting camera", [MiniMaxH3Reference(kind="video", media=_video(49))])

    assert seen["runs"][0].pad_token_id == VIDEO_PAD_TOKEN
    assert IMAGE_PAD_TOKEN not in seen["token_ids"]
    assert seen["token_ids"].count(VIDEO_PAD_TOKEN) > 0
    # One tower run for the whole video, whose grid_thw declares its groups.
    assert len(seen["runs"]) == 1
    assert int(seen["runs"][0].grid_thw[0, 0]) == 3


def test_video_reference_timestamp_text_is_tagged_text_and_the_blocks_video():
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    _timestamps, per_block = _grid_for(m, _video(49))

    out = enc.encode_reference_request("a drifting camera", [MiniMaxH3Reference(kind="video", media=_video(49))])
    tags = out["token_tags"]

    label_len = len(tok("<Video 1>: "))
    stamp_len = len(tok("<0.2 seconds>"))
    assert torch.all(tags[:label_len] == MINIMAX_H3_TEXT_TAG)
    # The first timestamp is TEXT, and the vision block right after it VIDEO.
    assert torch.all(tags[label_len:label_len + stamp_len] == MINIMAX_H3_TEXT_TAG)
    block = tags[label_len + stamp_len:label_len + stamp_len + per_block + 2]
    assert torch.all(block == MINIMAX_H3_VIDEO_TAG)


def test_audio_reference_is_a_label_with_no_vision_block():
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    img = torch.rand(32, 32, 3)

    with_audio = enc.encode_reference_request("sing it", [
        MiniMaxH3Reference(kind="image", media=img),
        MiniMaxH3Reference(kind="audio", has_audio=True),
    ])
    without = enc.encode_reference_request("sing it", [MiniMaxH3Reference(kind="image", media=img)])

    # An audio reference contributes EXACTLY its label's tokens, nothing more.
    assert with_audio["context"].shape[1] == without["context"].shape[1] + len(tok("<Audio 1>: "))
    assert torch.all(with_audio["token_tags"][-len(tok("sing it")) - len(tok("<Audio 1>: ")):
                                              -len(tok("sing it"))] == MINIMAX_H3_TEXT_TAG)


def test_audio_only_references_need_no_vision_tower():
    # A waveform never reaches the conditioner, so a reference list with no
    # visual media must encode on a text-only checkpoint.
    pytest.importorskip("transformers")
    m = _tiny_h3_module()  # no vision tower
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")

    out = enc.encode_reference_request("sing it", [MiniMaxH3Reference(kind="audio", has_audio=True)])

    expected = len(tok("<Audio 1>: ")) + len(tok("sing it"))
    assert out["context"].shape[1] == expected
    assert torch.all(out["token_tags"] == MINIMAX_H3_TEXT_TAG)


def test_mixed_reference_presentation_keeps_packed_order():
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    seen: dict = {}
    inner = enc.encode_presentation
    enc.encode_presentation = lambda token_ids, vision_runs=None: (
        seen.setdefault("runs", vision_runs), inner(token_ids, vision_runs=vision_runs),
    )[1]

    out = enc.encode_reference_request("blend them", [
        MiniMaxH3Reference(kind="image", media=torch.rand(32, 32, 3)),
        MiniMaxH3Reference(kind="video", media=_video(49), has_audio=True),
        MiniMaxH3Reference(kind="audio", has_audio=True),
    ])

    # Two tower runs, in packed order: the image first, then the video. The
    # audio reference produces none.
    assert [run.pad_token_id for run in seen["runs"]] == [IMAGE_PAD_TOKEN, VIDEO_PAD_TOKEN]
    assert int(seen["runs"][0].grid_thw[0, 0]) == 1
    assert int(seen["runs"][1].grid_thw[0, 0]) == 3
    assert out["context"].shape[1] == out["token_tags"].shape[0]
    assert torch.isfinite(out["context"]).all()


def test_bite_check_a_video_reference_is_not_presented_as_a_single_image():
    # BITE CHECK for the whole video branch: if a video were routed through
    # the image path (one block, one pad run, grid_t == 1), the presentation
    # would be strictly SHORTER than three timestamped blocks and carry no
    # <|video_pad|> at all.
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    tok = MiniMaxH3Tokenizer()
    enc = MiniMaxH3TextEncoder(m, tok, device="cpu")
    video = _video(49)

    as_video = enc.encode_reference_request("pan left", [MiniMaxH3Reference(kind="video", media=video)])
    as_image = enc.encode_reference_request("pan left", [MiniMaxH3Reference(kind="image", media=video[0])])

    assert as_video["context"].shape[1] > as_image["context"].shape[1]


def test_image_only_reference_request_is_unchanged_by_the_video_branch():
    # Bystander: the existing image-only presentation must still be exactly
    # encode_request's, token for token.
    pytest.importorskip("transformers")
    m = _tiny_h3_vl_module()
    enc = MiniMaxH3TextEncoder(m, MiniMaxH3Tokenizer(), device="cpu")
    imgs = [torch.rand(32, 32, 3), torch.rand(16, 16, 3)]

    via_request = enc.encode_request("blend them", images=imgs)
    via_reference = enc.encode_reference_request(
        "blend them", [MiniMaxH3Reference(kind="image", media=img) for img in imgs],
    )
    torch.testing.assert_close(via_request["context"], via_reference["context"], rtol=0, atol=0)
    assert torch.equal(via_request["token_tags"], via_reference["token_tags"])
