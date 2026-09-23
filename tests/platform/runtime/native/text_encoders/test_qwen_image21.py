from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.te_detect import detect_te_config
from src.platform.runtime.native.errors import NativeEngineUnsupportedError
from vendor.gpl.comfyui.ops import pick_operations
from src.platform.runtime.native.text_encoders.loader import _SPECS, _build_config, _make_encoder
from src.platform.runtime.native.text_encoders.qwen3 import Qwen3Model
from src.platform.runtime.native.text_encoders.qwen_image21 import QwenImage21TextEncoder
from src.platform.runtime.native.text_encoders.tokenization import (
    QWEN_IMAGE21_SYSTEM_PROMPT,
    QwenImage21Tokenizer,
)

_TINY_CFG = {
    "hidden_size": 16, "num_layers": 3, "vocab_size": 151936,
    "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 8,
    "intermediate_size": 32,
}

_TINY_VISION_CFG = {
    "vision_hidden_size": 8, "vision_intermediate_size": 16,
    "vision_num_layers": 2, "vision_num_heads": 2,
    "vision_patch_size": 2, "vision_temporal_patch_size": 2,
    "vision_spatial_merge_size": 2, "vision_num_position_embeddings": 16,
    "vision_deepstack_indexes": (0,),
}
_VL_CFG = {**_TINY_CFG, "vision": True, **_TINY_VISION_CFG}


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


def test_role_and_layer_index_is_last_layer():
    m = _tiny_module()
    enc = QwenImage21TextEncoder(m, QwenImage21Tokenizer(), device="cpu")
    assert enc.role == "qwen3vl_8b"
    assert enc._layer == 2


def test_tap_is_last_layer_output_without_final_norm():
    m = _tiny_module()
    ids = torch.tensor([[5, 6, 7, 8]])
    mask = torch.ones(1, 4, dtype=torch.long)

    x = m.model.embed_tokens(ids).float()
    cos, sin = m.model._rope(4, x.device, x.dtype)
    causal = torch.empty(4, 4).fill_(torch.finfo(x.dtype).min / 4).triu_(1)
    for layer in m.model.layers:
        x = layer(x, cos, sin, causal)
    manual_last = x.clone()

    fwd = m(ids, attention_mask=mask, layers_to_extract=(2,), capture="output").squeeze(1)
    assert torch.allclose(manual_last, fwd, atol=1e-4)
    assert not torch.allclose(m.model.norm(manual_last), fwd, atol=1e-4)


def test_template_rendering():
    pytest.importorskip("transformers")
    tok = QwenImage21Tokenizer()
    ids, _mask, prefix_len = tok(["a cat"], device="cpu")
    text = tok._tok.decode(ids[0].tolist())
    assert text.startswith("<|im_start|>system")
    assert QWEN_IMAGE21_SYSTEM_PROMPT in text
    assert "a cat" in text
    assert text.rstrip().endswith("<|im_start|>assistant")
    assert prefix_len == tok._prefix_len


def test_prefix_strips_only_the_system_turn():
    pytest.importorskip("transformers")
    tok = QwenImage21Tokenizer()
    ids, _mask, prefix_len = tok(["a cat"], device="cpu")
    kept = tok._tok.decode(ids[0, prefix_len:].tolist())
    assert kept.startswith("<|im_start|>user\na cat")
    assert QWEN_IMAGE21_SYSTEM_PROMPT not in kept


def test_prefix_len_unaffected_by_image_count():
    pytest.importorskip("transformers")
    tok = QwenImage21Tokenizer()
    _ids, _mask, no_image_len = tok(["x"], device="cpu")
    _ids1, _mask1, one_image_len = tok.tokenize_with_images("x", num_images=1, device="cpu")
    _ids3, _mask3, three_image_len = tok.tokenize_with_images("x", num_images=3, device="cpu")
    assert no_image_len == one_image_len == three_image_len == tok._prefix_len


def test_tokenize_with_images_labels_each_reference():
    pytest.importorskip("transformers")
    tok = QwenImage21Tokenizer()
    ids, _mask, _prefix_len = tok.tokenize_with_images("blend them", num_images=2, device="cpu")
    text = tok._tok.decode(ids[0].tolist())
    assert "<image1>" in text
    assert "<image2>" in text
    assert text.count("<|vision_start|><|image_pad|><|vision_end|>") == 2


def test_tokenize_with_images_rejects_zero_images():
    pytest.importorskip("transformers")
    tok = QwenImage21Tokenizer()
    with pytest.raises(ValueError):
        tok.tokenize_with_images("x", num_images=0, device="cpu")


def test_encode_strips_system_turn_and_returns_flat_context():
    pytest.importorskip("transformers")
    m = _tiny_module()
    tok = QwenImage21Tokenizer()
    enc = QwenImage21TextEncoder(m, tok, device="cpu")
    ids, mask, prefix_len = tok(["a cat"], device="cpu")

    out = enc.encode(["a cat"])
    assert out["context"].ndim == 3
    assert out["context"].shape == (1, ids.shape[1] - prefix_len, 16)
    assert out["attention_mask"].shape == (1, ids.shape[1] - prefix_len)
    assert set(out) == {"context", "attention_mask"}


def test_encode_images_without_vision_tower_raises():
    m = _tiny_module()
    enc = QwenImage21TextEncoder(m, QwenImage21Tokenizer(), device="cpu")
    with pytest.raises(NativeEngineUnsupportedError):
        enc.encode(["a cat"], images=[torch.rand(16, 16, 3)])


def test_encode_images_batch_greater_than_one_raises():
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    enc = QwenImage21TextEncoder(m, QwenImage21Tokenizer(), device="cpu")
    with pytest.raises(ValueError, match="one prompt"):
        enc.encode(["a", "b"], images=[torch.rand(16, 16, 3)])


def test_encode_with_images_returns_image_slots():
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    enc = QwenImage21TextEncoder(m, QwenImage21Tokenizer(), device="cpu")
    img_a, img_b = torch.rand(16, 16, 3), torch.rand(16, 16, 3)

    out = enc.encode(["blend them"], images=[img_a, img_b])
    assert "image_slots" in out
    slots = out["image_slots"]
    assert len(slots) == 2
    assert slots[0] < slots[1]
    assert all(0 <= s <= out["context"].shape[1] for s in slots)
    assert torch.isfinite(out["context"]).all()


def test_encode_with_images_keep_vision_true_has_no_slots_and_is_longer():
    pytest.importorskip("transformers")
    m = _tiny_vl_module()
    enc = QwenImage21TextEncoder(m, QwenImage21Tokenizer(), device="cpu")
    img = torch.rand(16, 16, 3)

    dropped = enc.encode(["x"], images=[img], keep_vision=False)
    kept = enc.encode(["x"], images=[img], keep_vision=True)
    assert "image_slots" not in kept
    assert kept["context"].shape[1] > dropped["context"].shape[1]


def test_build_config_sets_5e6_rope_theta_for_qwen3vl_8b():
    te_config = {"te_type": "qwen3vl", "variant": "qwen3vl_8b", "hidden_size": 4096,
                 "num_layers": 36, "vocab_size": 151936}
    cfg = _build_config(te_config, {})
    assert cfg["rope_theta"] == 5000000.0


def test_build_config_leaves_plain_qwen3_8b_rope_theta_unset():
    te_config = {"te_type": "qwen3", "variant": "qwen3_8b", "hidden_size": 4096,
                 "num_layers": 36, "vocab_size": 151936}
    cfg = _build_config(te_config, {})
    assert "rope_theta" not in cfg


def test_detect_config_variant_routes_encoder_dispatch():
    sd = {
        "model.embed_tokens.weight": torch.zeros(260, 4096),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(128),
        "model.visual.blocks.0.attn.qkv.weight": torch.zeros(4, 4),
    }
    c = detect_te_config(sd)
    assert c["variant"] == "qwen3vl_8b"

    m = _tiny_module()
    enc = _make_encoder("qwen3vl", "qwen3vl_8b", m, "cpu")
    assert isinstance(enc, QwenImage21TextEncoder)
