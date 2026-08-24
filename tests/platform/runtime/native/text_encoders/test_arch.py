"""Construction + forward smoke tests and post_load buffer sanity."""

from __future__ import annotations

import pytest
import torch

from vendor.gpl.comfyui.ops import disable_weight_init as ops
from vendor.gpl.comfyui.ops import manual_cast
from src.platform.runtime.native.text_encoders.clip_l import CLIPLModel
from src.platform.runtime.native.text_encoders.qwen3 import NATIVE_QWEN3_TE_BF16_ENV, Qwen3Model
from src.platform.runtime.native.text_encoders.t5xxl import T5XXLModel


def _build(model_cls, cfg, ops_ns=ops):
    m = model_cls.from_config(cfg, ops_ns)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02)
    m.post_load()
    return m


QWEN_CFG = {"hidden_size": 64, "num_layers": 4, "vocab_size": 100, "intermediate_size": 128,
            "num_attention_heads": 2, "num_key_value_heads": 1, "head_dim": 32,
            "te_type": "qwen3", "variant": "qwen3_4b"}
T5_CFG = {"hidden_size": 64, "num_layers": 3, "vocab_size": 100, "d_kv": 16, "num_heads": 4,
          "d_ff": 128, "te_type": "t5xxl", "variant": "t5xxl"}
CLIP_CFG = {"hidden_size": 64, "num_layers": 3, "vocab_size": 100, "num_attention_heads": 4,
            "intermediate_size": 128, "te_type": "clip_l", "variant": "clip_l"}


def test_qwen3_forward_shape():
    m = _build(Qwen3Model, QWEN_CFG)
    ids = torch.randint(0, 100, (2, 10))
    mask = torch.ones(2, 10, dtype=torch.long)
    mask[:, 7:] = 0
    out = m(ids, attention_mask=mask, layers_to_extract=[1, 2, 3])
    assert out.shape == (2, 3, 10, 64)  # [B, len(layers), S, H]
    assert torch.isfinite(out).all()


def test_qwen3_post_load_inv_freq_sanity():
    m = _build(Qwen3Model, QWEN_CFG)
    inv = m.model.inv_freq
    assert inv.shape == (QWEN_CFG["head_dim"] // 2,)
    assert torch.isfinite(inv).all()
    assert (inv != 0).any()
    # inv_freq is strictly decreasing (1 / theta^(k/d)).
    assert bool((inv[1:] <= inv[:-1]).all())
    assert inv[0].item() == pytest.approx(1.0)


def test_qwen3_inv_freq_recomputed_not_meta():
    # A fresh (unloaded) module has an empty inv_freq buffer; post_load fills it.
    m = Qwen3Model.from_config(QWEN_CFG, ops)
    for p in m.parameters():
        torch.nn.init.normal_(p, std=0.02)
    m.post_load()
    assert m.model.inv_freq.device.type != "meta"
    assert torch.isfinite(m.model.inv_freq).all()


def test_qwen3_rejects_too_few_layers():
    # Message is family-agnostic (Klein/Krea-2/Z-Image are 36 layers, Anima is
    # 28, MiniMax-H3 is 50/64 — no single layer count is right to hardcode).
    m = _build(Qwen3Model, QWEN_CFG)  # 4 layers
    with pytest.raises(ValueError, match=r"needs layers \[9, 18, 27\] but this checkpoint has only 4"):
        m(torch.randint(0, 100, (1, 5)), layers_to_extract=(9, 18, 27))


def test_t5_forward_shape_masked_and_unmasked():
    m = _build(T5XXLModel, T5_CFG)
    ids = torch.randint(0, 100, (2, 12))
    assert m(ids, attention_mask=None).shape == (2, 12, 64)
    mask = torch.cat([torch.ones(2, 8), torch.zeros(2, 4)], dim=1).long()
    out = m(ids, attention_mask=mask)
    assert out.shape == (2, 12, 64)
    assert torch.isfinite(out).all()


def test_clip_pooled_at_eos():
    m = _build(CLIPLModel, CLIP_CFG)
    ids = torch.randint(0, 100, (2, 77))
    eos = torch.tensor([5, 9])
    pooled = m(ids, eos)
    assert pooled.shape == (2, 64)
    assert torch.isfinite(pooled).all()


def test_encode_stacking_matches_layer_hidden_states():
    """encode()'s [B,S,3H] context is exactly the 3 layer states concatenated."""
    from src.platform.runtime.native.text_encoders.qwen3 import KLEIN_LAYERS, Qwen3TextEncoder

    m = _build(Qwen3Model, {**QWEN_CFG, "num_layers": 28})
    fixed_ids = torch.randint(0, 100, (2, 12))
    fixed_mask = torch.ones(2, 12, dtype=torch.long)

    class _StubTok:
        def __call__(self, texts, device="cpu"):
            return fixed_ids, fixed_mask

    enc = Qwen3TextEncoder(m, _StubTok(), variant="qwen3_4b")
    ctx = enc.encode(["x", "y"])["context"]

    stacked = m(fixed_ids, attention_mask=fixed_mask, layers_to_extract=KLEIN_LAYERS)  # [B,3,S,H]
    H = QWEN_CFG["hidden_size"]
    assert ctx.shape == (2, 12, 3 * H)
    # Feature block k of the context equals the k-th extracted layer state.
    for k in range(3):
        assert torch.equal(ctx[..., k * H:(k + 1) * H], stacked[:, k])


def _linear_input_dtypes(monkeypatch, model, ids):
    """Every activation dtype actually observed at a `manual_cast.Linear`
    boundary (`forward_comfy_cast_weights`'s `input.dtype`) during one
    forward -- not merely the dtype of the entry cast, which would pass even
    if a later fp32 island re-promoted `x` before it ever reached a Linear.
    """
    seen: list[torch.dtype] = []
    orig = manual_cast.Linear.forward_comfy_cast_weights

    def _spy(self, input):
        seen.append(input.dtype)
        return orig(self, input)

    monkeypatch.setattr(manual_cast.Linear, "forward_comfy_cast_weights", _spy)
    model(ids, layers_to_extract=[1])
    return seen


def test_qwen3_te_bf16_flag_off_by_default_keeps_fp32_at_linear_boundary(monkeypatch):
    monkeypatch.delenv(NATIVE_QWEN3_TE_BF16_ENV, raising=False)
    m = _build(Qwen3Model, QWEN_CFG, manual_cast)
    dtypes = _linear_input_dtypes(monkeypatch, m, torch.randint(0, 100, (1, 5)))
    assert dtypes
    assert all(dt == torch.float32 for dt in dtypes)


def test_qwen3_te_bf16_flag_on_holds_bf16_at_every_linear_boundary(monkeypatch):
    # The whole point: prove bf16 survives past the entry cast, through RoPE,
    # the attention mask, every RMSNorm and residual add, all the way to
    # every Linear this forward calls -- not just at the entry point, which
    # is exactly the shape of bug this flag has been bitten by twice before
    # (H3's fp32 AdaLN, Krea-2's f32 residual: a stray fp32 tensor meeting a
    # bf16 one silently re-promotes the result to fp32).
    monkeypatch.setenv(NATIVE_QWEN3_TE_BF16_ENV, "on")
    m = _build(Qwen3Model, QWEN_CFG, manual_cast)
    dtypes = _linear_input_dtypes(monkeypatch, m, torch.randint(0, 100, (1, 5)))
    assert dtypes
    assert all(dt == torch.bfloat16 for dt in dtypes)


def test_qwen3_te_bf16_unknown_policy_warns_and_falls_back_to_fp32(monkeypatch, caplog):
    monkeypatch.setenv(NATIVE_QWEN3_TE_BF16_ENV, "bogus")
    m = _build(Qwen3Model, QWEN_CFG, manual_cast)
    with caplog.at_level("WARNING"):
        dtypes = _linear_input_dtypes(monkeypatch, m, torch.randint(0, 100, (1, 5)))
    assert all(dt == torch.float32 for dt in dtypes)
    assert "unknown" in caplog.text.lower()
