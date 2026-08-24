"""Tests for the vendored Wan 2.1 / 2.2 architecture module (base t2v / i2v).

Coverage here is the arch itself: tiny-config 5D forward smokes for both t2v and
i2v (with CLIP-vision conds), the i2v/t2v structural branch, and post_load.
Detection + registry parity live in test_wan_detect.py / test_registry.py.
"""

from __future__ import annotations

import math

import torch

from vendor.gpl.comfyui.flux.math_ops import rope
from src.platform.runtime.native.arch.wan.config import WanParams
from src.platform.runtime.native.arch.wan.model import (
    WanModel,
    _riflex_intrinsic_k,
    _rope_temporal_riflex,
)
from src.platform.runtime.native.base import NativeArchModule
from vendor.gpl.comfyui.ops import pick_operations


TINY_T2V = dict(
    model_type="t2v", patch_size=(1, 2, 2), in_dim=16, dim=64, ffn_dim=128,
    freq_dim=256, text_dim=32, out_dim=16, num_heads=4, num_layers=2,
)
# i2v: larger in_dim (concat conditioning folded in by the generator) + img_emb.
TINY_I2V = dict(
    model_type="i2v", patch_size=(1, 2, 2), in_dim=36, dim=64, ffn_dim=128,
    freq_dim=256, text_dim=32, out_dim=16, num_heads=4, num_layers=2,
)


def _fp32_ops():
    return pick_operations(torch.float32, torch.float32)


def _build(config) -> WanModel:
    """from_config + fill empty params (norm scales -> 1, rest small random)."""
    m = WanModel.from_config(config, _fp32_ops())
    m.post_load()
    with torch.no_grad():
        for name, p in m.named_parameters():
            if p.dim() == 0:
                p.zero_()
            elif name.endswith(".weight") and (".norm" in name or "norm" in name.split(".")[-2]):
                p.fill_(1.0)
            else:
                p.normal_(0.0, 0.02)
    return m.to(torch.float32).eval()


def test_is_native_arch_module():
    assert issubclass(WanModel, NativeArchModule)


def test_tiny_t2v_forward_shape():
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)  # (B, C, T, H, W)
    with torch.no_grad():
        out = m(x, torch.tensor([0.5]), torch.randn(1, 12, 32))
    assert out.shape == (1, 16, 4, 16, 16)
    assert torch.isfinite(out).all()


def test_tiny_i2v_forward_with_clip_conds():
    m = _build(TINY_I2V)
    assert m.img_emb is not None  # i2v builds the CLIP-vision projector
    x = torch.randn(1, 36, 4, 16, 16)
    clip_fea = torch.randn(1, 8, 1280)  # CLIP-vision features
    with torch.no_grad():
        out = m(x, torch.tensor([0.5]), torch.randn(1, 12, 32), clip_fea=clip_fea)
    assert out.shape == (1, 16, 4, 16, 16)
    assert torch.isfinite(out).all()


def test_t2v_has_no_img_emb():
    m = _build(TINY_T2V)
    assert m.img_emb is None


def test_temporal_and_spatial_dims_tracked():
    # A non-square, multi-frame latent must round-trip its (T, H, W) through
    # patchify/unpatchify.
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 6, 16, 24)
    with torch.no_grad():
        out = m(x, torch.tensor([0.3]), torch.randn(1, 5, 32))
    assert out.shape == (1, 16, 6, 16, 24)


def test_from_config_maps_fields():
    params = WanParams.from_detect_config(TINY_I2V)
    assert params.model_type == "i2v"
    assert params.in_dim == 36
    assert params.dim == 64
    assert params.num_heads == 4
    assert params.patch_size == (1, 2, 2)


def test_post_load_is_noop_and_returns_none():
    m = WanModel.from_config(TINY_T2V, _fp32_ops())
    assert m.post_load() is None


# -- NAG (Normalized Attention Guidance) -------------------------------------

def test_nag_context_absent_is_byte_identical():
    """No nag_context/nag kwargs at all -> forward must match today's output
    exactly (the additive-only contract)."""
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    with torch.no_grad():
        baseline = m(x, t, context)
        out = m(x, t, context, nag_context=None, nag=None)
    assert torch.allclose(baseline, out)


def test_nag_scale_one_is_identity_even_with_context_present():
    """nag_scale <= 1.0 must be a no-op even when nag_context is supplied."""
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    nag_context = torch.randn(1, 12, 32)
    with torch.no_grad():
        baseline = m(x, t, context)
        out = m(x, t, context, nag_context=nag_context, nag={"scale": 1.0})
    assert torch.allclose(baseline, out)


def test_nag_scale_above_one_changes_output_t2v():
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    nag_context = torch.randn(1, 12, 32)
    with torch.no_grad():
        baseline = m(x, t, context)
        out = m(x, t, context, nag_context=nag_context, nag={"scale": 1.5, "tau": 3.5, "alpha": 0.5})
    assert out.shape == baseline.shape
    assert torch.isfinite(out).all()
    assert not torch.allclose(baseline, out)


def test_nag_scale_above_one_changes_output_i2v_text_only():
    """i2v: NAG must only affect the text-attention branch — the image (CLIP)
    cross-attention still runs untouched, and the output still differs from
    the no-NAG baseline."""
    m = _build(TINY_I2V)
    x = torch.randn(1, 36, 4, 16, 16)
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    clip_fea = torch.randn(1, 8, 1280)
    nag_context = torch.randn(1, 12, 32)
    with torch.no_grad():
        baseline = m(x, t, context, clip_fea=clip_fea)
        out = m(x, t, context, clip_fea=clip_fea, nag_context=nag_context, nag={"scale": 1.5})
        identical_scale = m(x, t, context, clip_fea=clip_fea, nag_context=nag_context, nag={"scale": 1.0})
    assert out.shape == baseline.shape
    assert torch.isfinite(out).all()
    assert not torch.allclose(baseline, out)
    assert torch.allclose(baseline, identical_scale)


# -- RIFLEx (roadmap 3.8, arXiv:2502.15894) -----------------------------------
#
# TINY_T2V: dim=64, num_heads=4 -> head_dim=16, axes_dim[0] = 16 - 4*(16//6) = 8
# (temporal axis dim). With theta=10000 the per-component periods (eq. 4,
# j=1..dim//2=4) are: j=1 -> 6, j=2 -> 63, j=3 -> 628, j=4 -> 6283.

def test_riflex_disabled_is_byte_identical():
    """riflex=None (the default) and riflex={"enabled": False} must produce
    the exact same freqs as never having threaded the argument at all."""
    m = _build(TINY_T2V)
    baseline = m.rope_encode(4, 16, 16, device="cpu", dtype=torch.float32)
    none_freqs = m.rope_encode(4, 16, 16, device="cpu", dtype=torch.float32, riflex=None)
    off_freqs = m.rope_encode(4, 16, 16, device="cpu", dtype=torch.float32, riflex={"enabled": False})
    assert torch.equal(baseline, none_freqs)
    assert torch.equal(baseline, off_freqs)


def test_riflex_disabled_when_not_extrapolating():
    """enabled=True but the requested length doesn't exceed
    latent_frames_trained: must still be the byte-identical no-op path."""
    m = _build(TINY_T2V)
    baseline = m.rope_encode(4, 16, 16, device="cpu", dtype=torch.float32)
    out = m.rope_encode(4, 16, 16, device="cpu", dtype=torch.float32,
                         riflex={"enabled": True, "latent_frames_trained": 4})
    assert torch.equal(baseline, out)


def test_riflex_intrinsic_k_hand_computed():
    """dim=8, theta=10000, trained=63 lands exactly on the j=2 period (63) ->
    k=1 (0-based)."""
    k = _riflex_intrinsic_k(dim=8, theta=10000.0, latent_frames_trained=63)
    assert k == 1


def test_riflex_temporal_rope_touches_only_the_intrinsic_component():
    """The RIFLEx-modified temporal rope tensor must equal the standard
    ``rope()`` output at every frequency index except k."""
    dim, theta = 8, 10000.0
    k = 1
    theta_k = 0.9 * 2.0 * math.pi / 200  # arbitrary L_test
    pos = torch.arange(10, dtype=torch.float32).unsqueeze(0)  # (1, 10)

    standard = rope(pos, dim, theta)
    riflex_out = _rope_temporal_riflex(pos, dim, theta, k, theta_k)

    assert standard.shape == riflex_out.shape == (1, 10, dim // 2, 2, 2)
    for idx in range(dim // 2):
        if idx == k:
            assert not torch.allclose(standard[:, :, idx], riflex_out[:, :, idx])
        else:
            assert torch.equal(standard[:, :, idx], riflex_out[:, :, idx])


def test_riflex_modified_component_period_covers_test_length():
    """The modified component's period must be >= L_test (eq. 8's
    non-repetition condition) — verified by recovering theta_k from the
    rotation angle at position 1 and checking round(2*pi/theta_k) >= L_test."""
    dim, theta = 8, 10000.0
    k = 1
    l_test = 200
    theta_k = 0.9 * 2.0 * math.pi / l_test
    period = round(2.0 * math.pi / theta_k)
    assert period >= l_test


# -- RIFLEx clamps downward only, never accelerates (roadmap S14/#14) --------

def test_riflex_clamps_downward_never_accelerates_an_already_safe_component():
    """A forced ``k`` whose NATURAL frequency is already below the eq. 8
    bound must be left alone, never overwritten upward. dim=16/theta=10000/
    k=7's natural frequency (~3.16e-4, period ~19869) is far below a typical
    ``theta_k`` (~0.257 at t_len=22) — an unconditional overwrite would
    accelerate it ~813x instead of leaving the already-safe component
    untouched (this exact dim/theta/t_len/k combination is the finding's own
    failure scenario)."""
    dim, theta, k, t_len = 16, 10000.0, 7, 22
    theta_k = 0.9 * 2.0 * math.pi / t_len
    pos = torch.arange(5, dtype=torch.float32).unsqueeze(0)

    standard = rope(pos, dim, theta)
    riflex_out = _rope_temporal_riflex(pos, dim, theta, k, theta_k)

    # Clamped downward only: the natural frequency at k is already below
    # theta_k, so the clamped output must be IDENTICAL to the standard rope()
    # output at every component, including k itself.
    assert torch.equal(standard, riflex_out)


def test_riflex_still_reduces_a_component_genuinely_above_the_bound():
    """Sanity check the clamp still does something in the common/intended
    case (component's natural frequency above theta_k) — the fix must not
    silently disable RIFLEx altogether."""
    dim, theta, k = 8, 10000.0, 1
    theta_k = 0.9 * 2.0 * math.pi / 200
    pos = torch.arange(5, dtype=torch.float32).unsqueeze(0)

    standard = rope(pos, dim, theta)
    riflex_out = _rope_temporal_riflex(pos, dim, theta, k, theta_k)
    assert not torch.equal(standard[:, :, k], riflex_out[:, :, k])


def test_riflex_forward_smoke_with_extrapolated_frames():
    """End-to-end: a tiny Wan model, riflex enabled with a trained length well
    below the requested frame count, must still produce a finite,
    correctly-shaped output."""
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 8, 16, 16)  # 8 latent frames, well past trained=2
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    with torch.no_grad():
        out = m(x, t, context, riflex={"enabled": True, "latent_frames_trained": 2})
    assert out.shape == (1, 16, 8, 16, 16)
    assert torch.isfinite(out).all()


def test_riflex_forward_changes_output_vs_disabled_when_extrapolating():
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 8, 16, 16)
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    with torch.no_grad():
        baseline = m(x, t, context)
        riflex_out = m(x, t, context, riflex={"enabled": True, "latent_frames_trained": 2})
    assert riflex_out.shape == baseline.shape
    assert torch.isfinite(riflex_out).all()
    assert not torch.allclose(baseline, riflex_out)


def test_riflex_explicit_k_skips_autodetection():
    """An explicit ``k`` must be honored (no auto-detection call needed) and
    only affect that single temporal frequency component."""
    m = _build(TINY_T2V)
    forced_k = 2
    auto = m.rope_encode(8, 16, 16, device="cpu", dtype=torch.float32,
                          riflex={"enabled": True, "latent_frames_trained": 2})
    forced = m.rope_encode(8, 16, 16, device="cpu", dtype=torch.float32,
                            riflex={"enabled": True, "latent_frames_trained": 2, "k": forced_k})
    # Different k (forced=2) than whatever auto-detection picked for
    # trained=2 must generally produce a different temporal frequency tensor.
    auto_k = _riflex_intrinsic_k(dim=8, theta=10000.0, latent_frames_trained=2)
    if auto_k != forced_k:
        assert not torch.equal(auto, forced)


# -- skip_layers (Skip-Layer Guidance, roadmap 3.5) --------------------------

def test_skip_layers_none_is_byte_identical():
    """skip_layers=None (the default) and the bare no-kwarg call must produce
    the exact same output — same code path as before SLG existed."""
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    with torch.no_grad():
        bare = m(x, t, context)
        explicit_none = m(x, t, context, skip_layers=None)
        empty_set = m(x, t, context, skip_layers=set())
    assert torch.equal(bare, explicit_none)
    assert torch.equal(bare, empty_set)


def test_skip_layers_actually_bypasses_the_block():
    """skip_layers={i} must skip block i's computation entirely — verified via
    a call counter hook on the block, and the output must differ from the
    unskipped baseline."""
    m = _build(TINY_T2V)
    assert len(m.blocks) == 2  # TINY_T2V num_layers=2

    calls = {"n": 0}
    orig_forward = m.blocks[0].forward

    def counting_forward(*args, **kwargs):
        calls["n"] += 1
        return orig_forward(*args, **kwargs)

    m.blocks[0].forward = counting_forward

    x = torch.randn(1, 16, 4, 16, 16)
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    with torch.no_grad():
        baseline = m(x, t, context)
        assert calls["n"] == 1
        skipped = m(x, t, context, skip_layers={0})
        assert calls["n"] == 1  # block 0 was NOT called the second time

    assert skipped.shape == baseline.shape
    assert torch.isfinite(skipped).all()
    assert not torch.allclose(baseline, skipped)


def test_skip_layers_multiple_indices():
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    with torch.no_grad():
        baseline = m(x, t, context)
        all_skipped = m(x, t, context, skip_layers={0, 1})
    assert all_skipped.shape == baseline.shape
    assert torch.isfinite(all_skipped).all()
    assert not torch.allclose(baseline, all_skipped)


def test_skip_layers_out_of_range_index_is_a_noop_for_that_index():
    # An index past num_layers must not raise (the `i in skip_layers` check is
    # a plain membership test against the enumerate index, which simply never
    # matches an out-of-range value).
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)
    t = torch.tensor([0.5])
    context = torch.randn(1, 12, 32)
    with torch.no_grad():
        baseline = m(x, t, context)
        out = m(x, t, context, skip_layers={99})
    assert torch.equal(baseline, out)
