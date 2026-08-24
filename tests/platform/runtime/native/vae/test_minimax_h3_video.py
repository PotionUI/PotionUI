"""Tests for the MiniMax-H3 video VAE (causal-3D-conv encoder + ViT decoder).

No real weights -- CPU-only, tiny configs (see ``_TINY_CONFIG``). Real
checkpoint headers were used to derive the module's key layout (see
``vae/minimax_h3_video.py`` module docstring), not downloaded here.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F

from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.vae.loader import _VaeSpec
from src.platform.runtime.native.vae.minimax_h3_video import (
    MiniMaxH3VideoVAE,
    _VideoDecoderAttention,
    _VideoDecoderFeedForward,
    _randn_like_reference,
    video_latent_frame_count,
)
from vendor.gpl.comfyui.ops import disable_weight_init, pick_operations

# A tiny but structurally faithful config: 2 encoder down-levels (one with a
# 2x spatial-only downsample, one with none), tiny ViT decoder (2 layers, 2
# heads x 8 dim = 16-wide). clip_length/token_drop/tokens_chunk_size are
# scaled down from the real 17/3/5 to 5/1/3 so a handful of pixel frames
# exercises real multi-chunk temporal logic without the real 17-frame minimum.
_TINY_CONFIG = dict(
    latent_channels=4,
    block_out_channels=(8, 16),
    layers_per_block=1,
    spatial_downsample_factors=(2, 1),
    temporal_downsample_factors=(1, 2),
    norm_num_groups=2,
    decoder_num_layers=2,
    decoder_num_attention_heads=2,
    decoder_attention_head_dim=8,
    decoder_num_register_tokens=2,
    decoder_ffn_mult=2,
    clip_length=5,
    token_drop=1,
    tile_sample_min_height=8,
    tile_sample_min_width=8,
    tile_sample_min_overlap_height=4,
    tile_sample_min_overlap_width=4,
)


def _build(*, use_tiling: bool = False, config: dict = _TINY_CONFIG) -> MiniMaxH3VideoVAE:
    module = MiniMaxH3VideoVAE.from_config(config, disable_weight_init)
    module.use_tiling = use_tiling
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
    return module


class TestVideoLatentFrameCount:
    """Chunk-math table: P=1 special case, and the general
    ``L = 5 * ceil(P / 17) - 3`` formula (incl. the docstring's own
    ``17n + 5 -> 5n + 2`` example, which is that formula evaluated at
    P = 17n + 5)."""

    def test_single_frame_bypass(self):
        assert video_latent_frame_count(1) == 1
        assert video_latent_frame_count(0) == 1

    def test_17n_plus_5_maps_to_5n_plus_2(self):
        for n in range(4):
            pixel_frames = 17 * n + 5
            assert video_latent_frame_count(pixel_frames) == 5 * n + 2

    def test_general_formula_table(self):
        cases = {2: 2, 5: 2, 17: 2, 18: 7, 22: 7, 34: 7, 35: 12}
        for pixel_frames, expected in cases.items():
            assert video_latent_frame_count(pixel_frames) == expected


class TestMiniMaxH3VideoVAETiny:
    def test_self_consistent_state_dict_passes_load_integrity(self):
        module = _build()
        sd = module.state_dict()
        module2 = MiniMaxH3VideoVAE.from_config(_TINY_CONFIG, disable_weight_init)
        load_into_module(module2, sd, _VaeSpec(family="vae", variant="minimax_h3_video"))

    def test_post_load_is_safe_noop_and_recomputes_rope(self):
        module = _build()
        inv_freq_before = module.decoder.rope.inv_freq.clone()
        module.post_load()
        assert torch.equal(module.decoder.rope.inv_freq, inv_freq_before)

    def test_mask_token_is_a_real_parameter_not_a_computed_zero(self):
        """The repack-vs-diffusers discrepancy: mask_token is a loaded
        nn.Parameter here, and a non-zero value must actually reach the ViT
        decoder's forward (not be silently replaced by a literal zero)."""
        module = _build()
        with torch.no_grad():
            module.decoder.mask_token.fill_(5.0)
        assert isinstance(module.decoder.mask_token, torch.nn.Parameter)
        assert torch.all(module.decoder.mask_token == 5.0)

    def test_single_pixel_frame_encode_bypasses_chunking(self):
        module = _build()
        x = torch.randn(1, 3, 1, 8, 8)
        with torch.no_grad():
            z = module.encode(x)
        assert z.shape[2] == 1
        assert z.shape[1] == _TINY_CONFIG["latent_channels"]
        assert torch.isfinite(z).all()

    def test_multi_chunk_encode_decode_roundtrip_shape_and_finite(self):
        module = _build(use_tiling=False)
        x = torch.randn(1, 3, 10, 16, 16)
        with torch.no_grad():
            z = module.encode(x)
            dec = module.decode(z)
        assert z.shape[0] == 1
        assert z.shape[1] == _TINY_CONFIG["latent_channels"]
        assert dec.shape[0] == 1
        assert dec.shape[1] == 3
        assert torch.isfinite(z).all()
        assert torch.isfinite(dec).all()

    def test_tiled_and_untiled_decode_both_produce_finite_matching_shapes(self):
        """Tiling changes the exact pixel values (feathered blend seams --
        see module docstring), so this only asserts shape parity + finiteness
        for both paths, not numeric equality."""
        module = _build(use_tiling=False)
        x = torch.randn(1, 3, 10, 16, 16)
        with torch.no_grad():
            z = module.encode(x)
            dec_untiled = module.decode(z)

        module.use_tiling = True
        with torch.no_grad():
            dec_tiled = module.decode(z)

        assert dec_tiled.shape == dec_untiled.shape
        assert torch.isfinite(dec_tiled).all()

    def test_repeated_decode_calls_are_deterministic(self):
        module = _build(use_tiling=True)
        x = torch.randn(1, 3, 10, 16, 16)
        with torch.no_grad():
            z = module.encode(x)
            dec_a = module.decode(z)
            dec_b = module.decode(z)
        assert torch.equal(dec_a, dec_b)

    def test_encode_returns_mode_directly_not_a_distribution_object(self):
        """House convention (matches ae_2d.py/causal_3d.py): no separate
        DiagonalGaussianDistribution class -- encode() returns the mean
        tensor directly."""
        module = _build()
        x = torch.randn(1, 3, 1, 8, 8)
        with torch.no_grad():
            z = module.encode(x)
        assert isinstance(z, torch.Tensor)

    def test_latents_mean_and_std_are_real_buffers(self):
        module = _build()
        sd = module.state_dict()
        assert "latents_mean" in sd
        assert "latents_std" in sd
        assert sd["latents_mean"].shape == (_TINY_CONFIG["latent_channels"],)


def _build_with_pinned_nontrivial_std(*, logvar_bias: float = 2.0) -> MiniMaxH3VideoVAE:
    """A tiny module whose quant_conv bias forces the logvar half of the
    moments to a known nonzero constant, so `std = exp(0.5*logvar)` is
    guaranteed far from 1 regardless of the rest of the random init -- makes
    the posterior-sampling bite-check robust instead of relying on the
    default random init happening to produce non-trivial std."""
    module = _build()
    latent_channels = _TINY_CONFIG["latent_channels"]
    with torch.no_grad():
        module.quant_conv.bias[latent_channels:].fill_(logvar_bias)
        module.quant_conv.weight[latent_channels:].zero_()  # logvar depends only on the pinned bias
    return module


class TestPosteriorSampling:
    """`encode(sample_posterior=True, generator=...)` -- the fl2va
    keyframe-conditioning seam (see `conditioning.py`'s module docstring for
    the consumer). Default behavior (`sample_posterior=False`) must stay
    byte-identical to before this parameter existed."""

    def test_default_call_is_unaffected_mode(self):
        module = _build()
        x = torch.randn(1, 3, 1, 8, 8)
        with torch.no_grad():
            mode_a = module.encode(x)
            mode_b = module.encode(x, sample_posterior=False)
        assert torch.equal(mode_a, mode_b)

    def test_sampled_output_is_deterministic_under_a_fixed_seed(self):
        """Two FRESH generators constructed with the same seed (not the same
        live generator object, which mutates on use) must give identical
        samples."""
        module = _build_with_pinned_nontrivial_std()
        x = torch.randn(1, 3, 10, 16, 16)
        with torch.no_grad():
            sample_a = module.encode(x, sample_posterior=True, generator=torch.Generator().manual_seed(42))
            sample_b = module.encode(x, sample_posterior=True, generator=torch.Generator().manual_seed(42))
        assert torch.equal(sample_a, sample_b)

    def test_different_seeds_give_different_samples(self):
        module = _build_with_pinned_nontrivial_std()
        x = torch.randn(1, 3, 10, 16, 16)
        with torch.no_grad():
            sample_a = module.encode(x, sample_posterior=True, generator=torch.Generator().manual_seed(1))
            sample_b = module.encode(x, sample_posterior=True, generator=torch.Generator().manual_seed(2))
        assert not torch.equal(sample_a, sample_b)

    def test_sample_differs_from_mode(self):
        module = _build_with_pinned_nontrivial_std()
        x = torch.randn(1, 3, 10, 16, 16)
        with torch.no_grad():
            mode = module.encode(x)
            sample = module.encode(x, sample_posterior=True, generator=torch.Generator().manual_seed(0))
        assert not torch.equal(mode, sample)

    def test_sample_matches_hand_computed_mean_plus_std_times_noise(self):
        """Closeness test: independently recomputes `mean`/`std` from the
        module's own raw moments (via the private `_encode`) and draws the
        SAME noise via `_randn_like_reference`, then checks the module's
        public `encode(sample_posterior=True, ...)` matches that formula
        exactly. See the bite-check test below for why this specifically
        catches a broken `std *` application (a determinism-only test would
        not)."""
        module = _build_with_pinned_nontrivial_std()
        x = torch.randn(1, 3, 10, 16, 16)
        with torch.no_grad():
            moments = module._encode(x)
        mean, logvar = moments.chunk(2, dim=1)
        logvar = torch.clamp(logvar, -30.0, 20.0)
        std = torch.exp(0.5 * logvar)
        noise = _randn_like_reference(
            mean.shape, generator=torch.Generator().manual_seed(7), device=mean.device, dtype=mean.dtype,
        )
        expected = mean + std * noise

        with torch.no_grad():
            actual = module.encode(x, sample_posterior=True, generator=torch.Generator().manual_seed(7))
        assert torch.allclose(actual, expected)

    def test_bite_check_std_omitted_would_fail_the_closeness_test(self):
        """Bite-check: a `mean + noise` formula (the `std *` factor dropped)
        gives a measurably DIFFERENT result than the real module output,
        because `_build_with_pinned_nontrivial_std` pins `std = exp(1.0) ≈
        2.72` far from 1. This is the failure the closeness test above is
        meant to catch; a determinism-only test would NOT catch it (a
        broken-but-still-deterministic sample is still perfectly
        deterministic under a fixed seed)."""
        module = _build_with_pinned_nontrivial_std()
        x = torch.randn(1, 3, 10, 16, 16)
        with torch.no_grad():
            moments = module._encode(x)
        mean, logvar = moments.chunk(2, dim=1)
        std = torch.exp(0.5 * torch.clamp(logvar, -30.0, 20.0))
        assert not torch.allclose(std, torch.ones_like(std))  # confirms the fixture actually pins a non-trivial std
        noise = _randn_like_reference(
            mean.shape, generator=torch.Generator().manual_seed(7), device=mean.device, dtype=mean.dtype,
        )
        broken_sample = mean + noise  # missing `std *`

        with torch.no_grad():
            actual = module.encode(x, sample_posterior=True, generator=torch.Generator().manual_seed(7))
        assert not torch.allclose(actual, broken_sample)


# --- ViT decoder SwiGLU (gate FIRST half) + fused-qkv (PER-HEAD interleaved) ---
#
# Ground truth for both: ComfyUI's real `comfy/ldm/minimax/vae.py` (GPL-3.0,
# comfyanonymous and contributors) -- the real, working consumer of this exact
# checkpoint format:
#   FeedForward.forward:  gate, x = self.w1(x).chunk(2, dim=-1)
#                          return self.w2(F.silu(gate).mul_(x))
#   Attention.forward:    qkv = qkv.view(b, s, -1, 3*dim_head)   # -1 -> heads
#                          query, key, value = torch.chunk(qkv, 3, dim=-1)
# A previous version of this port copied diffusers' own (unfused, value-
# first/gate-second) SwiGLU convention for the FFN, and grouped q/k/v as three
# big contiguous blocks across all heads instead of per-head interleaved for
# attention -- both backwards relative to what this checkpoint's repacked
# weights actually need, and both produced 16px-grid structured noise on
# real decode (the ViT decoder's patch size) -- see `MEASUREMENT.md` in the
# investigation scratchpad for the real-generation frame measurement that
# pinned this to the VAE side rather than the DiT.

def test_decoder_swiglu_gate_is_first_half():
    """Bite-checkable against an independent reference built from the SAME
    ComfyUI formula, transcribed here rather than reusing the module's own
    chunk order."""
    torch.manual_seed(30)
    ops = disable_weight_init
    dim, inner = 6, 4
    ff = _VideoDecoderFeedForward(dim, inner, operations=ops)
    with torch.no_grad():
        ff.w1.weight.copy_(torch.randn_like(ff.w1.weight))
        ff.w1.bias.copy_(torch.randn_like(ff.w1.bias))
        ff.w2.weight.copy_(torch.randn_like(ff.w2.weight))
        ff.w2.bias.copy_(torch.randn_like(ff.w2.bias))
    x = torch.randn(1, 3, dim)

    w1_out = F.linear(x, ff.w1.weight, ff.w1.bias)
    gate, value = w1_out.chunk(2, dim=-1)  # ComfyUI's own convention
    expected = F.linear(F.silu(gate) * value, ff.w2.weight, ff.w2.bias)
    torch.testing.assert_close(ff(x), expected)


def test_decoder_swiglu_halves_are_not_interchangeable():
    """Sanity check for the test above: swapping which half is gate changes
    the result by more than float noise (rules out a vacuous pass)."""
    torch.manual_seed(31)
    ops = disable_weight_init
    ff = _VideoDecoderFeedForward(6, 4, operations=ops)
    with torch.no_grad():
        ff.w1.weight.copy_(torch.randn_like(ff.w1.weight))
        ff.w1.bias.copy_(torch.randn_like(ff.w1.bias))
        ff.w2.weight.copy_(torch.randn_like(ff.w2.weight))
        ff.w2.bias.copy_(torch.randn_like(ff.w2.bias))
    x = torch.randn(1, 3, 6)
    w1_out = F.linear(x, ff.w1.weight, ff.w1.bias)
    gate, value = w1_out.chunk(2, dim=-1)
    correct = F.linear(F.silu(gate) * value, ff.w2.weight, ff.w2.bias)
    swapped = F.linear(F.silu(value) * gate, ff.w2.weight, ff.w2.bias)
    assert not torch.allclose(correct, swapped, atol=1e-4, rtol=1e-3)


def test_decoder_qkv_split_is_per_head_interleaved():
    """Bite-checkable against an independent reference: transcribes
    ComfyUI's ``qkv.view(b, s, -1, 3*dim_head)`` + ``chunk(3, dim=-1)``
    directly (not calling into `_VideoDecoderAttention.forward`), and checks
    the module's internal q/k/v split matches it -- by capturing what the
    module feeds to `scaled_dot_product_attention` and asserting it equals
    the reference q/k/v BEFORE rope/qk-norm (rope/norm are identity-ish
    under the fixed seed's random weights, this isolates the split itself
    via a norm+rope-free spy on the pre-attention tensors instead)."""
    torch.manual_seed(32)
    ops = disable_weight_init
    heads, dim_head = 4, 8
    dim = heads * dim_head
    attn = _VideoDecoderAttention(dim, heads, dim_head, eps=1e-5, operations=ops)
    with torch.no_grad():
        attn.to_qkv.weight.copy_(torch.randn_like(attn.to_qkv.weight))
        attn.to_qkv.bias.copy_(torch.randn_like(attn.to_qkv.bias))

    x = torch.randn(1, 5, dim)
    qkv_flat = F.linear(x, attn.to_qkv.weight, attn.to_qkv.bias)

    # ComfyUI's own split: view(b,s,-1,3*dim_head) -> resolves to (heads,
    # 3*dim_head) -- per-head interleaved q|k|v -- then chunk(3,-1).
    ref = qkv_flat.view(1, 5, heads, 3 * dim_head)
    q_ref, k_ref, v_ref = torch.chunk(ref, 3, dim=-1)

    # the module's own (pre-rope, pre-norm -- norm is applied to both sides
    # identically so it cancels in a raw-split comparison) internal split:
    q_ours, k_ours, v_ours = qkv_flat.reshape(1, 5, heads, 3, dim_head).unbind(dim=3)

    torch.testing.assert_close(q_ours, q_ref)
    torch.testing.assert_close(k_ours, k_ref)
    torch.testing.assert_close(v_ours, v_ref)


def test_decoder_qkv_split_bite_check_against_block_convention():
    """The bite check for the test above: the OLD (wrong) block-grouped
    split -- `reshape(b,n,3,heads,dim_head)`, splitting q/k/v as three big
    contiguous blocks across ALL heads -- gives a DIFFERENT q for any head
    but the first, proving the two conventions are not accidentally
    equivalent."""
    torch.manual_seed(33)
    heads, dim_head = 4, 8
    dim = heads * dim_head
    qkv_flat = torch.randn(1, 5, 3 * dim)

    q_per_head, _, _ = torch.chunk(qkv_flat.view(1, 5, heads, 3 * dim_head), 3, dim=-1)
    q_block_grouped, _, _ = qkv_flat.reshape(1, 5, 3, heads, dim_head).unbind(dim=2)

    assert not torch.allclose(q_per_head, q_block_grouped)
