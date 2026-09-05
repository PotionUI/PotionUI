"""Z-Image (NextDiT) output head — it must see only the real image tokens.

The joint stack runs over ``[caption ; caption-pad ; image ; image-pad]``. Only
the ``h_tok * w_tok`` image rows are unpatchified, and ``_FinalLayer`` is
token-local (per-token LayerNorm, broadcast adaLN scale, per-token Linear), so
the head must be applied to the slice, not to the whole sequence.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.z_image.model import ZImageDiT
from src.platform.runtime.native.sampling.step_cache import FirstBlockCache
from vendor.gpl.comfyui.ops import pick_operations

# dim must be >= 256: the timestep embedder always emits 256 and the adaLN
# Linear consumes min(dim, 256). dim 256 / n_heads 4 -> head_dim 64 == sum(axes_dims).
ZTINY = {
    "image_model": "lumina2", "in_channels": 16, "dim": 256, "cap_feat_dim": 8,
    "n_layers": 2, "n_refiner_layers": 1, "n_heads": 4, "n_kv_heads": 4,
    "intermediate_size": 128, "axes_dims": (32, 16, 16), "patch_size": 2,
}
PAD_MULTIPLE = 32


def _build(dtype=torch.float32) -> ZImageDiT:
    m = ZImageDiT.from_config(ZTINY, pick_operations(dtype, dtype))
    sd = {}
    for k, v in m.state_dict().items():
        if not v.is_floating_point():
            sd[k] = v.clone()
        elif "norm" in k and (k.endswith(".weight") or k.endswith(".scale")):
            sd[k] = torch.ones_like(v)
        else:
            sd[k] = torch.randn_like(v) * 0.02
    m.load_state_dict(sd)
    return m.eval().to(dtype)


def _inputs(bsz: int, cap_len: int, h: int, w: int, dtype=torch.float32):
    x = torch.randn(bsz, 16, h, w, dtype=dtype)
    t = torch.full((bsz,), 0.5, dtype=torch.float32)
    ctx = torch.randn(bsz, cap_len, ZTINY["cap_feat_dim"], dtype=dtype)
    return x, t, ctx


def _capture_joint(m: ZImageDiT):
    """Capture the joint-stack output, i.e. what the head saw before this fix."""
    seen = {}

    def hook(_module, _args, output):
        seen["joint"] = output

    handle = m.layers[-1].register_forward_hook(hook)
    return seen, handle


def _tokens_per_side(m: ZImageDiT, h: int, w: int) -> tuple[int, int]:
    """Token grid after forward's circular pad up to a patch multiple."""
    p = m.patch_size
    return -(-h // p), -(-w // p)


def _head_on_full_sequence(m: ZImageDiT, joint, t, x, cap_len: int, h: int, w: int):
    """The pre-fix tail: head over the whole sequence, then slice + unpatchify.

    ``forward`` circular-pads odd H/W up to a patch multiple and crops the result
    back, so the reference patchifies at the padded size and crops the same way.
    """
    p = m.patch_size
    h_tok, w_tok = _tokens_per_side(m, h, w)
    adaln_input = m.t_embedder((1.0 - t) * m.time_scale, dtype=x.dtype)
    projected = m.final_layer(joint, adaln_input)
    img_tokens = projected[:, cap_len:cap_len + h_tok * w_tok]
    out = img_tokens.view(x.shape[0], h_tok, w_tok, p, p, m.out_channels)
    out = out.permute(0, 5, 1, 3, 2, 4).reshape(x.shape[0], m.out_channels, h_tok * p, w_tok * p)
    return -out[:, :, :h, :w]


def _spy_on_head(m: ZImageDiT):
    """Record the row count the head is asked to project, per call."""
    rows: list[int] = []
    orig = m.final_layer.forward

    def spy(x, c):
        rows.append(x.shape[1])
        return orig(x, c)

    m.final_layer.forward = spy
    return rows


def _cap_len(ctx_len: int) -> int:
    return ctx_len + (-ctx_len) % PAD_MULTIPLE


def test_head_receives_only_real_image_rows():
    m = _build()
    x, t, ctx = _inputs(bsz=1, cap_len=3, h=16, w=16)
    rows = _spy_on_head(m)
    with torch.no_grad():
        m(x, t, ctx)
    assert rows == [(16 // 2) * (16 // 2)]


def test_head_row_count_excludes_x_pad_tokens():
    # 6x10 latent -> 3x5 = 15 image tokens, padded up to 32 by x_pad_token.
    m = _build()
    x, t, ctx = _inputs(bsz=2, cap_len=7, h=6, w=10)
    rows = _spy_on_head(m)
    with torch.no_grad():
        m(x, t, ctx)
    assert rows == [15]


def test_odd_latent_is_padded_cropped_and_head_sees_the_padded_token_grid():
    # 7x11 latent -> circular-padded to 8x12 -> 4x6 = 24 image tokens (padded up
    # to 32 by x_pad_token), and the result is cropped back to 7x11.
    m = _build()
    x, t, ctx = _inputs(bsz=2, cap_len=3, h=7, w=11)
    rows = _spy_on_head(m)
    seen, handle = _capture_joint(m)
    try:
        with torch.no_grad():
            out = m(x, t, ctx)
            forward_rows = list(rows)  # the reference below calls the head again
            expected = _head_on_full_sequence(m, seen["joint"], t, x, _cap_len(3), 7, 11)
    finally:
        handle.remove()
    assert forward_rows == [24]
    assert out.shape == (2, ZTINY["in_channels"], 7, 11)
    assert out.shape == expected.shape
    assert torch.allclose(out, expected, atol=1e-6, rtol=0)


def test_output_matches_head_over_full_sequence():
    for bsz, cap_len, h, w in ((1, 3, 16, 16), (2, 7, 6, 10), (2, 3, 8, 12), (2, 3, 7, 11)):
        m = _build()
        x, t, ctx = _inputs(bsz, cap_len, h, w)
        seen, handle = _capture_joint(m)
        try:
            with torch.no_grad():
                out = m(x, t, ctx)
                expected = _head_on_full_sequence(m, seen["joint"], t, x, _cap_len(cap_len), h, w)
        finally:
            handle.remove()
        assert out.shape == expected.shape
        assert torch.allclose(out, expected, atol=1e-6, rtol=0), (bsz, cap_len, h, w)


def test_output_matches_head_over_full_sequence_bf16():
    m = _build(torch.bfloat16)
    x, t, ctx = _inputs(2, 7, 6, 10, dtype=torch.bfloat16)
    seen, handle = _capture_joint(m)
    try:
        with torch.no_grad():
            out = m(x, t, ctx)
            expected = _head_on_full_sequence(m, seen["joint"], t, x, _cap_len(7), 6, 10)
    finally:
        handle.remove()
    assert out.dtype is torch.bfloat16
    assert torch.allclose(out.float(), expected.float(), atol=2e-2, rtol=1e-2)


def test_computed_path_runs_the_head_once_per_step():
    m = _build()
    x, t, ctx = _inputs(bsz=1, cap_len=3, h=16, w=16)
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    rows = _spy_on_head(m)
    with torch.no_grad():
        m(x, t, ctx, step_cache=cache)
        m(torch.randn_like(x) * 5.0, t, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}
    assert rows == [64, 64]


def test_skip_path_bypasses_the_head_and_reuses_the_cached_output():
    m = _build()
    x, t, ctx = _inputs(bsz=1, cap_len=3, h=16, w=16)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    rows = _spy_on_head(m)
    with torch.no_grad():
        first = m(x, t, ctx, step_cache=cache)
        second = m(x, t, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert rows == [64]
    assert torch.equal(second, first)
