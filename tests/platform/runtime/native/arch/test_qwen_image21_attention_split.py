"""Split attention on the Qwen-Image-2.1 arch: prove the masked-prefix /
unmasked-target split ``_Attention.forward`` now runs is mathematically
identical to a single call over the full dense block-causal mask -- the
split only changes which kernel handles which query rows, never the result.

A true diffusers-reference parity case for this (2+ reference blocks) is not
buildable the way ``test_qwen_image21_parity.py`` builds its other cases:
that file's reference forward takes VLM-embedded image placeholders inside
``encoder_hidden_states`` (real content from a vision-language encoder), not
raw per-tensor ``ref_latents`` -- see that file's module docstring. This file
instead verifies the split directly against a hand-rolled single-mask call
built from the SAME attention module's own weights/RoPE, which exercises the
identical code the parity file's own reference-image splicing tests
(``test_forward_matches_reference_fp32`` et al.) run through.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.qwen_image21.model import _block_causal_mask
from src.platform.runtime.native.attention import attention as _dispatch_attention
from vendor.gpl.comfyui.flux.math_ops import apply_rope1

from .test_qwen_image21_model import TINY, _build_ready


def _qkv(attn, x, pe):
    b, n, _ = x.shape

    def split(t):
        return t.view(b, n, attn.heads, -1).transpose(1, 2)

    q, k, v = split(attn.to_q(x)), split(attn.to_k(x)), split(attn.to_v(x))
    q, k = attn.norm_q(q), attn.norm_k(k)
    return apply_rope1(q, pe), apply_rope1(k, pe), v


def _monolithic_call(attn, x, pe, mask):
    q, k, v = _qkv(attn, x, pe)
    with torch.no_grad():
        out = _dispatch_attention(q, k, v, mask=mask)
    out = out.transpose(1, 2).reshape(x.shape[0], x.shape[1], -1)
    return attn.to_out[0](out)


def test_split_attention_equals_single_masked_call_no_refs():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)

    hidden_states, pe, prefix_len, token_kind, key_valid = m.build_sequence(x, context, None, [])
    mask = _block_causal_mask(token_kind, key_valid)
    attn = m.transformer_blocks[0].attn

    with torch.no_grad():
        split_out = attn(hidden_states, pe, mask, prefix_len, target_key_mask=None)
    monolithic_out = _monolithic_call(attn, hidden_states, pe, mask)

    torch.testing.assert_close(split_out, monolithic_out, atol=1e-5, rtol=1e-5)


def test_split_attention_equals_single_masked_call_two_refs_and_slots():
    """Two reference image blocks spliced into the text run (Qwen-Image-Edit
    layout) -- exercises both the prefix-masked path (text + both ref
    blocks) and the target-unmasked path in one call."""
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 2, 2)
    ref_a = torch.randn(1, 8, 1, 1)
    ref_b = torch.randn(1, 8, 1, 1)
    context = torch.randn(1, 4, 12)

    hidden_states, pe, prefix_len, token_kind, key_valid = m.build_sequence(
        x, context, None, [ref_a, ref_b], image_slots=[1, 3],
    )
    mask = _block_causal_mask(token_kind, key_valid)
    attn = m.transformer_blocks[0].attn

    with torch.no_grad():
        split_out = attn(hidden_states, pe, mask, prefix_len, target_key_mask=None)
    monolithic_out = _monolithic_call(attn, hidden_states, pe, mask)

    torch.testing.assert_close(split_out, monolithic_out, atol=1e-5, rtol=1e-5)


def test_split_attention_equals_single_masked_call_with_padded_text():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    real_len, pad_len = 3, 2
    context = torch.randn(1, real_len + pad_len, 12)
    attention_mask = torch.zeros(1, real_len + pad_len, dtype=torch.long)
    attention_mask[:, :real_len] = 1

    hidden_states, pe, prefix_len, token_kind, key_valid = m.build_sequence(
        x, context, attention_mask, [],
    )
    mask = _block_causal_mask(token_kind, key_valid)
    target_key_mask = key_valid[:, None, None, :]
    attn = m.transformer_blocks[0].attn

    with torch.no_grad():
        split_out = attn(hidden_states, pe, mask, prefix_len, target_key_mask=target_key_mask)
    monolithic_out = _monolithic_call(attn, hidden_states, pe, mask)

    torch.testing.assert_close(split_out, monolithic_out, atol=1e-5, rtol=1e-5)


def test_end_to_end_forward_unaffected_by_the_split():
    """Full-model sanity: the split is an internal attention-dispatch detail,
    invisible at the arch's own forward() contract."""
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ref = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    out = m(x, torch.tensor([0.5]), context, ref_latents=[ref], image_slots=[2])
    assert out.shape == x.shape
    assert torch.isfinite(out).all()
