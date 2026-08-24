"""Qwen2.5-VL-7B text encoder (Qwen-Image) tests.

Covers: arch construction/forward (qkv bias, no q/k norm, last-normed output),
post_load RoPE sanity, template+prefix-drop golden ids, detection disambiguation
vs qwen3 / qwen3-vl, real-header key parity, and full synthetic load + encode.
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from src.platform.runtime.native.detect.te_detect import detect_te_config  # noqa: E402
from src.platform.runtime.native.errors import NativeEngineUnsupportedError  # noqa: E402
from vendor.gpl.comfyui.ops import disable_weight_init as ops  # noqa: E402
from src.platform.runtime.native.text_encoders.loader import _SPECS, _build_config  # noqa: E402
from src.platform.runtime.native.text_encoders.qwen25_vl import Qwen25VLModel  # noqa: E402
from src.platform.runtime.native.text_encoders.qwen_vl_vision import VISION_NUM_HEADS as _VISION_NUM_HEADS  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[5]
_REAL = _REPO_ROOT / "models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors"

TINY = {"hidden_size": 64, "num_layers": 3, "vocab_size": 152064, "intermediate_size": 128,
        "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 32}


def _build(cfg):
    m = Qwen25VLModel.from_config(cfg, ops)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02)
    m.post_load()
    return m


def test_arch_has_qkv_bias_and_no_qk_norm():
    m = _build(TINY)
    attn = m.model.layers[0].self_attn
    assert attn.q_proj.bias is not None
    assert attn.k_proj.bias is not None
    assert attn.v_proj.bias is not None
    assert attn.o_proj.bias is None          # o_proj has no bias in the checkpoint
    assert not hasattr(attn, "q_norm")       # qwen2.5 has no q/k norm
    assert not hasattr(attn, "k_norm")


def test_forward_returns_last_normed_hidden():
    m = _build(TINY)
    ids = torch.randint(0, 152064, (2, 10))
    mask = torch.ones(2, 10, dtype=torch.long)
    mask[:, 7:] = 0
    out = m(ids, attention_mask=mask)
    assert out.shape == (2, 10, 64)          # [B, S, H] — single last hidden, not a stack
    assert torch.isfinite(out).all()


def test_post_load_inv_freq_sanity():
    m = _build(TINY)
    inv = m.model.inv_freq
    assert inv.shape == (TINY["head_dim"] // 2,)
    assert torch.isfinite(inv).all() and (inv != 0).any()
    assert bool((inv[1:] <= inv[:-1]).all())


def test_template_prefix_drop_golden():
    pytest.importorskip("transformers")
    from src.platform.runtime.native.text_encoders.tokenization import Qwen25VLTokenizer

    tok = Qwen25VLTokenizer()
    ids, mask, prefix_len = tok(["a cat"])
    # Canonical template prefix is 34 tokens (verified == ComfyUI's dynamic template_end).
    assert prefix_len == 34
    # "a cat" full template = 41 tokens; kept = 41 - 34 = 7 (user text + assistant header).
    assert ids.shape[1] == 41
    kept = ids[0, prefix_len:].tolist()
    assert kept == [64, 8251, 151645, 198, 151644, 77091, 198]
    # No forced 512 padding (min_length=1): single prompt stays natural length.
    assert int(mask.sum()) == 41


def test_detection_disambiguates_qwen25_from_qwen3_variants():
    def qwen_sd(bias: bool, qnorm: bool, vision: bool, hidden=64, vocab=152064):
        sd = {"model.embed_tokens.weight": torch.zeros(vocab, hidden),
              "model.layers.0.self_attn.q_proj.weight": torch.zeros(hidden, hidden)}
        if bias:
            sd["model.layers.0.self_attn.q_proj.bias"] = torch.zeros(hidden)
        if qnorm:
            sd["model.layers.0.self_attn.q_norm.weight"] = torch.zeros(128)
        if vision:
            sd["model.visual.blocks.0.weight"] = torch.zeros(4)
        return sd

    # qwen2.5-vl: bias, no q_norm.
    assert detect_te_config(qwen_sd(bias=True, qnorm=False, vision=False))["te_type"] == "qwen25_vl"
    # qwen3 (Klein): q_norm, no bias, vocab 151936.
    assert detect_te_config(qwen_sd(bias=False, qnorm=True, vision=False, vocab=151936))["te_type"] == "qwen3"
    # qwen3-vl (Krea-2): q_norm + model.visual.*.
    assert detect_te_config(qwen_sd(bias=False, qnorm=True, vision=True, vocab=151936))["te_type"] == "qwen3vl"


def test_build_config_recovers_heads_and_intermediate():
    sd = {
        "model.layers.0.mlp.gate_proj.weight": torch.zeros(18944, 3584),
        "model.layers.0.self_attn.q_proj.weight": torch.zeros(3584, 3584),   # 28 heads * 128
        "model.layers.0.self_attn.k_proj.weight": torch.zeros(512, 3584),    # 4 kv * 128
    }
    cfg = _build_config({"te_type": "qwen25_vl", "hidden_size": 3584, "num_layers": 28, "vocab_size": 152064}, sd)
    assert cfg["intermediate_size"] == 18944
    assert cfg["num_attention_heads"] == 28
    assert cfg["num_key_value_heads"] == 4


def test_synthetic_load_strips_vision_and_drops_prefix(tmp_path):
    pytest.importorskip("transformers")
    from safetensors.torch import save_file

    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    # head_dim 128 so the loader's head derivation (shape // 128) matches.
    cfg = {"hidden_size": 128, "num_layers": 2, "vocab_size": 152064, "intermediate_size": 256,
           "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 128}
    m = Qwen25VLModel.from_config(cfg, ops)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02)
    sd = {k: v.detach().clone().to(torch.bfloat16) for k, v in m.state_dict().items()}
    # vision tower + lm_head must be stripped, not choke the load.
    sd["visual.blocks.0.attn.qkv.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)
    sd["lm_head.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)

    path = tmp_path / "qwen25vl.safetensors"
    save_file(sd, str(path))
    enc = load_text_encoder(str(path))
    assert enc.role == "qwen25_vl_7b"
    out = enc.encode(["a cat"])
    assert set(out) == {"context", "attention_mask"}
    assert out["context"].shape[2] == 128
    # 41-token template minus 34-token prefix = 7.
    assert out["context"].shape[1] == 7
    assert torch.isfinite(out["context"]).all()
    assert "pooled" not in out


# --- vision tower: construction, config plumbing, image-conditioned encode ---

_TINY_VISION_CFG = {
    "vision_hidden_size": 8, "vision_intermediate_size": 16,
    "vision_num_layers": 1, "vision_num_heads": 2,
}
# head_dim MUST stay 128: `_mrope`'s ROPE_DIMS=(16,24,24) split sums to 64
# (=head_dim//2) and is a fixed architectural constant, not parameterised by
# the LM's own (tiny-in-tests) hidden_size/num_layers/num_heads.
_LM_CFG_FOR_VISION = {
    "hidden_size": 128, "num_layers": 2, "vocab_size": 152064, "intermediate_size": 256,
    "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 128,
    "vision": True, **_TINY_VISION_CFG,
}


def test_vision_false_default_has_no_visual_attribute():
    m = Qwen25VLModel.from_config(TINY, ops)
    assert not hasattr(m, "visual")


def test_vision_true_builds_visual_tower_matching_lm_hidden_size():
    m = Qwen25VLModel.from_config(_LM_CFG_FOR_VISION, ops)
    assert hasattr(m, "visual")
    # merger's final Linear must project into the LM's hidden_size for splicing.
    assert m.visual.merger.mlp[-1].out_features == 128
    assert len(m.visual.blocks) == 1
    assert m.visual.blocks[0].attn.num_heads == 2


def test_build_config_recovers_vision_dims_when_requested():
    sd = {
        "visual.blocks.0.norm1.weight": torch.zeros(8),
        "visual.blocks.0.mlp.gate_proj.weight": torch.zeros(16, 8),
        "visual.blocks.1.norm1.weight": torch.zeros(8),
    }
    cfg = _build_config(
        {"te_type": "qwen25_vl", "hidden_size": 128, "num_layers": 2, "vocab_size": 152064, "vision": True},
        sd,
    )
    assert cfg["vision_hidden_size"] == 8
    assert cfg["vision_intermediate_size"] == 16
    assert cfg["vision_num_layers"] == 2


def test_build_config_skips_vision_derivation_when_not_requested():
    sd = {"visual.blocks.0.norm1.weight": torch.zeros(8)}
    cfg = _build_config(
        {"te_type": "qwen25_vl", "hidden_size": 128, "num_layers": 2, "vocab_size": 152064},
        sd,
    )
    assert "vision_hidden_size" not in cfg


# A checkpoint round-tripped through `load_text_encoder` gets re-DETECTED, not
# handed the original build config — and `vision_num_heads` is not
# checkpoint-derivable (see `Qwen25VLConfig.vision_num_heads`'s docstring), so
# the loader always assumes the production default (16). A checkpoint built
# with a DIFFERENT vision_num_heads (as `_LM_CFG_FOR_VISION` above uses, for the
# direct from_config tests) cannot round-trip through the loader — this second,
# loader-only config keeps num_heads at its real default (16) and picks
# vision_hidden_size=64 so head_dim (=64/16=4) stays divisible by 4, which the
# rotary-embedding halving (head_dim -> head_dim//2 -> //2 again, then doubled
# back twice) needs to land on an exact (not rounded-up) width at every step —
# the real model's head_dim=80 satisfies this the same way (80/4=20 exactly).
_LM_CFG_FOR_VISION_LOADER_ROUNDTRIP = {
    **_LM_CFG_FOR_VISION,
    "vision_hidden_size": 64, "vision_intermediate_size": 16, "vision_num_layers": 1,
    "vision_num_heads": _VISION_NUM_HEADS,
}


def _build_tiny_vision_checkpoint(tmp_path):
    from safetensors.torch import save_file

    m = Qwen25VLModel.from_config(_LM_CFG_FOR_VISION_LOADER_ROUNDTRIP, ops)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02)
    sd = {k: v.detach().clone().to(torch.bfloat16) for k, v in m.state_dict().items()}
    sd["lm_head.weight"] = torch.zeros(4, 4, dtype=torch.bfloat16)  # must still be stripped
    path = tmp_path / "qwen25vl_vision.safetensors"
    save_file(sd, str(path))
    return path


def test_loader_vision_true_loads_tower_and_encodes_with_image(tmp_path):
    pytest.importorskip("transformers")
    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    path = _build_tiny_vision_checkpoint(tmp_path)
    enc = load_text_encoder(str(path), vision=True)
    assert enc._has_vision

    img = torch.rand(32, 32, 3)  # big enough that the tiny tower's min_pixels floor is a no-op
    out = enc.encode(["make it night"], images=[img])
    assert set(out) == {"context", "attention_mask"}
    assert out["context"].shape[0] == 1
    assert out["context"].shape[2] == 128  # LM hidden_size
    assert out["context"].shape[1] == out["attention_mask"].shape[1]
    assert out["context"].shape[1] > 0
    assert torch.isfinite(out["context"]).all()
    assert "pooled" not in out


def test_loader_vision_false_ignores_visual_keys_in_the_same_checkpoint(tmp_path):
    pytest.importorskip("transformers")
    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    path = _build_tiny_vision_checkpoint(tmp_path)  # has visual.* keys
    enc = load_text_encoder(str(path), vision=False)
    assert not enc._has_vision
    out = enc.encode(["a cat"])  # unaffected: exactly the text-only path
    assert set(out) == {"context", "attention_mask"}


def test_encode_images_without_vision_tower_raises(tmp_path):
    pytest.importorskip("transformers")
    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    path = _build_tiny_vision_checkpoint(tmp_path)
    enc = load_text_encoder(str(path), vision=False)
    with pytest.raises(NativeEngineUnsupportedError):
        enc.encode(["a cat"], images=[torch.rand(32, 32, 3)])


def test_encode_images_wrong_count_raises(tmp_path):
    pytest.importorskip("transformers")
    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    path = _build_tiny_vision_checkpoint(tmp_path)
    enc = load_text_encoder(str(path), vision=True)
    with pytest.raises(ValueError, match="image"):
        enc.encode(["a cat"], images=[torch.rand(32, 32, 3), torch.rand(32, 32, 3)])  # template has 1 slot


def test_encode_images_batch_greater_than_one_raises(tmp_path):
    pytest.importorskip("transformers")
    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    path = _build_tiny_vision_checkpoint(tmp_path)
    enc = load_text_encoder(str(path), vision=True)
    with pytest.raises(ValueError, match="one prompt"):
        enc.encode(["a cat", "a dog"], images=[torch.rand(32, 32, 3)])


@pytest.mark.requires_models
@pytest.mark.skipif(not _REAL.is_file(), reason="real qwen2.5-vl checkpoint absent")
def test_real_header_key_parity():
    with open(_REAL, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
    real_keys = {k for k in header if k != "__metadata__"}

    cfg = {"hidden_size": 64, "num_layers": 28, "vocab_size": 152064, "intermediate_size": 128,
           "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 128}
    module = Qwen25VLModel.from_config(cfg, ops)
    mkeys = set(module.state_dict())
    spec = _SPECS["qwen25_vl"]
    bad_unexpected = [k for k in real_keys - mkeys if not spec.key_is_expected_unexpected(k)]
    bad_missing = [k for k in mkeys - real_keys if not spec.key_is_expected_missing(k)]
    assert not bad_unexpected, bad_unexpected[:10]
    assert not bad_missing, bad_missing[:10]


@pytest.mark.skipif(
    not (_REAL.is_file() and os.environ.get("POTIONUI_QWEN25VL_REALFILE")),
    reason="real qwen2.5-vl checkpoint absent or POTIONUI_QWEN25VL_REALFILE not set (slow, ~20s / 7GB)",
)
def test_real_file_loads_and_encodes():
    pytest.importorskip("transformers")
    from src.platform.runtime.native.text_encoders.loader import load_text_encoder

    enc = load_text_encoder(str(_REAL), device="cpu")
    out = enc.encode(["a cat"])
    assert out["context"].shape == (1, 7, 3584)   # 41-token template - 34 prefix, hidden 3584
    assert torch.isfinite(out["context"]).all()
    assert "pooled" not in out
