"""Boolean vs. integer text-padding masks on the Qwen-Image MMDiT forward.

``forward`` accepts any non-floating ``attention_mask`` (1/True = keep). The
padding-detection logic (trim-or-skip) only ever compares/indexes the mask, so
it tolerates bool tensors fine — but the additive-mask construction used to do
arithmetic subtraction on the raw mask (``mask - 1``), which PyTorch refuses
for a genuinely boolean tensor. These tests drive every padding shape through
both dtypes and assert bit-for-bit parity between them, plus parity against
the pre-fix integer formula reimplemented locally.
"""

from __future__ import annotations

import pytest
import torch

from .test_qwen_image_model import TINY, _build_ready


def _old_formula_additive_mask(int_mask: torch.Tensor, dtype: torch.dtype) -> torch.Tensor:
    """The pre-fix construction, valid only for an integer (0/1) mask —
    reimplemented here (not imported) so the test is a faithful "what did the
    old code produce" oracle, independent of the fix under test."""
    return (int_mask - 1).to(dtype) * torch.finfo(dtype).max


def _capture_sdpa_masks():
    """Patch scaled_dot_product_attention to record every attn_mask tensor it
    was called with (one per transformer block)."""
    seen = []
    real_attn = torch.nn.functional.scaled_dot_product_attention

    def spy(q, k, v, attn_mask=None, **kw):
        seen.append(None if attn_mask is None else attn_mask.clone())
        return real_attn(q, k, v, attn_mask=attn_mask, **kw)

    import src.platform.runtime.native.attention as attn_mod
    orig = attn_mod.F.scaled_dot_product_attention
    attn_mod.F.scaled_dot_product_attention = spy
    return seen, lambda: setattr(attn_mod.F, "scaled_dot_product_attention", orig)


# rows of 0/1 padding patterns exercised below, named for the trim-guard branch
# they hit: right-padded trims to the real prefix, left/middle-padded and
# ragged batches keep the full window with an additive mask.
PATTERNS = {
    "all_real": [[1, 1, 1, 1, 1]],
    "right_padded": [[1, 1, 1, 0, 0]],
    "left_padded": [[0, 0, 1, 1, 1]],
    "middle_padded": [[1, 1, 0, 0, 1, 1]],
    "single_real_token": [[1, 0, 0, 0, 0]],
    "ragged_batch": [[1, 1, 1, 0, 0], [1, 0, 0, 0, 0]],
}


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_boolean_and_integer_masks_are_equivalent(name):
    """For every padding shape: build the model and inputs once, run forward
    with an int mask and again with the bool view of the *same* pattern, and
    assert identical additive-mask tensors, identical sdpa masks, and
    identical output. The int-mask output must also match the pre-fix
    formula exactly (no drift for the code path that already worked)."""
    torch.manual_seed(0)
    m = _build_ready(TINY)
    rows = PATTERNS[name]
    l = len(rows[0])
    b = len(rows)
    x = torch.randn(b, 4, 1, 8, 8)
    t = torch.full((b,), 0.5)
    ctx = torch.randn(b, l, 12)
    int_mask = torch.tensor(rows, dtype=torch.long)
    bool_mask = int_mask.bool()

    seen_int, restore_int = _capture_sdpa_masks()
    try:
        out_int = m(x, t, ctx.clone(), attention_mask=int_mask.clone())
    finally:
        restore_int()

    seen_bool, restore_bool = _capture_sdpa_masks()
    try:
        out_bool = m(x, t, ctx.clone(), attention_mask=bool_mask.clone())
    finally:
        restore_bool()

    assert torch.equal(out_int, out_bool)
    assert len(seen_int) == len(seen_bool)
    for a, bmask in zip(seen_int, seen_bool):
        if a is None or bmask is None:
            assert a is None and bmask is None
        else:
            torch.testing.assert_close(a, bmask, atol=0.0, rtol=0.0)

    # Right-padded / all-real / single-real-token trim to a mask-free window;
    # everything else keeps an additive mask whose values must match the
    # pre-fix integer formula exactly (same finfo bound, same shape logic).
    if name in ("right_padded", "all_real", "single_real_token"):
        assert all(mask is None for mask in seen_int)
    else:
        assert any(mask is not None for mask in seen_int)
        # left/middle-padded skip the trim entirely (row_has_real_after_pad);
        # a ragged batch trims to the longest real row but keeps residual
        # padding in the shorter rows.
        no_trim = {"left_padded", "middle_padded"}
        if name in no_trim:
            txt_len = l
        else:
            txt_len = max(int(int_mask.sum(dim=1).max().item()), 1)
        trimmed = int_mask[:, :txt_len]
        expected = _old_formula_additive_mask(trimmed, x.dtype)
        found = next(mask for mask in seen_int if mask is not None)
        # sdpa mask is (B, 1, 1, seq_txt + seq_img), broadcast over heads/query
        # rows, with only the leading seq_txt slots carrying the text mask.
        torch.testing.assert_close(found[:, 0, 0, :txt_len], expected, atol=1e-30, rtol=0.0)


def test_all_padding_row_is_finite_and_dtype_consistent():
    """Documented behaviour: a row with no real tokens at all still trims to a
    1-token window (``max(real_len, 1)``) whose single slot is itself padding,
    so the additive mask is ``finfo.min`` everywhere in that row. The output
    stays finite (softmax over a uniformly-shifted row degrades to an even
    average rather than NaN) and is identical between bool and int masks."""
    torch.manual_seed(0)
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 5, 12)
    int_mask = torch.zeros(1, 5, dtype=torch.long)
    bool_mask = int_mask.bool()

    out_int = m(x, t, ctx.clone(), attention_mask=int_mask.clone())
    out_bool = m(x, t, ctx.clone(), attention_mask=bool_mask.clone())

    assert torch.isfinite(out_int).all()
    assert torch.equal(out_int, out_bool)


def test_boolean_mask_with_ref_latents_index_timestep_zero_matches_integer():
    """The ref_latents / timestep_zero_index edit path sits entirely below the
    mask normalisation and must be unaffected: a boolean mask alongside
    ref_latents produces the same shape and value as the equivalent int
    mask."""
    torch.manual_seed(0)
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 8, 8)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 5, 12)
    int_mask = torch.tensor([[1, 1, 1, 0, 0]], dtype=torch.long)
    bool_mask = int_mask.bool()
    kw = dict(ref_latents=[ref], ref_latents_method="index_timestep_zero")

    out_int = m(x, t, ctx.clone(), attention_mask=int_mask.clone(), **kw)
    out_bool = m(x, t, ctx.clone(), attention_mask=bool_mask.clone(), **kw)

    assert out_int.shape == x.shape
    assert torch.equal(out_int, out_bool)


def test_boolean_left_padded_mask_bites_against_the_pre_fix_arithmetic():
    """Bite check: the pre-fix construction did ``(mask - 1)`` directly on the
    (possibly boolean) mask. Left/middle padding never trims, so it always
    reaches that subtraction — reproducing it here proves a boolean mask on
    that branch raises without the fix."""
    torch.manual_seed(0)
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 5, 12)
    bool_mask = torch.tensor([[0, 0, 1, 1, 1]], dtype=torch.bool)

    with pytest.raises(RuntimeError, match="bool tensor"):
        (bool_mask - 1)

    # And the real forward call must NOT raise with the fix in place.
    out = m(x, t, ctx, attention_mask=bool_mask)
    assert torch.isfinite(out).all()
