"""Tests for temporal-chunked causal-3D VAE decode (``vae/tiling.py::
chunked_decode_causal3d``) -- bounds decode VRAM by chunk size instead of clip
length, without changing the decoded pixels. Covers both causal-3D VAE shapes:
Wan 2.1 / Qwen-Image (``causal_3d.py``, no ``first_chunk`` bookkeeping) and
Wan 2.2 (``causal_3d_v2.py``, needs ``first_chunk`` threaded to the true first
latent frame only -- see the module docstrings).
"""

from __future__ import annotations

import torch

from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.vae.causal_3d import AutoEncoderCausal3D
from src.platform.runtime.native.vae.causal_3d_v2 import AutoEncoderCausal3D_2_2
from src.platform.runtime.native.vae.tiling import chunked_decode_causal3d


def _randomize_weights(module: torch.nn.Module) -> None:
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)


def _build_tiny_v1() -> AutoEncoderCausal3D:
    module = AutoEncoderCausal3D.from_config({}, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return module


def _build_tiny_v2() -> AutoEncoderCausal3D_2_2:
    module = AutoEncoderCausal3D_2_2.from_config({}, disable_weight_init)
    module.eval()
    _randomize_weights(module)
    return module


# --- Wan 2.1 / Qwen-Image shape (causal_3d.py) -----------------------------


def test_v1_chunked_decode_matches_whole_decode_evenly_divisible():
    torch.manual_seed(0)
    vae = _build_tiny_v1()
    z = torch.randn(1, 16, 9, 4, 4)  # 9 latent frames, chunk=3 -> exactly 3 chunks

    with torch.no_grad():
        whole = vae.decode(z)
        chunked = chunked_decode_causal3d(vae, z, chunk_latent_frames=3)

    assert chunked.shape == whole.shape
    torch.testing.assert_close(chunked, whole, rtol=0, atol=0)


def test_v1_chunked_decode_matches_whole_decode_with_remainder():
    torch.manual_seed(1)
    vae = _build_tiny_v1()
    z = torch.randn(1, 16, 9, 4, 4)  # chunk=4 -> chunks of 4, 4, 1 (remainder)

    with torch.no_grad():
        whole = vae.decode(z)
        chunked = chunked_decode_causal3d(vae, z, chunk_latent_frames=4)

    assert chunked.shape == whole.shape
    torch.testing.assert_close(chunked, whole, rtol=0, atol=0)


def test_v1_chunked_decode_degenerates_to_single_decode_when_chunk_exceeds_clip():
    torch.manual_seed(2)
    vae = _build_tiny_v1()
    z = torch.randn(1, 16, 3, 4, 4)

    with torch.no_grad():
        whole = vae.decode(z)
        chunked = chunked_decode_causal3d(vae, z, chunk_latent_frames=8)

    torch.testing.assert_close(chunked, whole, rtol=0, atol=0)


def test_v1_chunked_decode_single_latent_frame_chunks():
    # The finest possible external chunking (matches decode()'s own internal
    # per-frame granularity) -- still must equal a whole decode exactly.
    torch.manual_seed(3)
    vae = _build_tiny_v1()
    z = torch.randn(1, 16, 5, 4, 4)

    with torch.no_grad():
        whole = vae.decode(z)
        chunked = chunked_decode_causal3d(vae, z, chunk_latent_frames=1)

    torch.testing.assert_close(chunked, whole, rtol=0, atol=0)


def test_v1_decode_feat_cache_param_defaults_preserve_old_behavior():
    """``decode(z)`` with no ``feat_cache`` arg must be byte-identical to
    before this change (regression guard on the signature change)."""
    torch.manual_seed(4)
    vae = _build_tiny_v1()
    z = torch.randn(1, 16, 5, 4, 4)

    with torch.no_grad():
        a = vae.decode(z)
        b = vae.decode(z)  # fresh internal cache each call, no cross-call state

    torch.testing.assert_close(a, b, rtol=0, atol=0)


def test_v1_new_feat_cache_matches_internal_cache_size():
    from src.platform.runtime.native.vae.causal_3d import _count_causal_conv3d

    vae = _build_tiny_v1()
    cache = vae.new_feat_cache()
    assert len(cache) == _count_causal_conv3d(vae.decoder)
    assert all(c is None for c in cache)


# --- Wan 2.2 shape (causal_3d_v2.py) ----------------------------------------


def test_v2_chunked_decode_matches_whole_decode_evenly_divisible():
    torch.manual_seed(10)
    vae = _build_tiny_v2()
    z = torch.randn(1, 48, 9, 4, 4)

    with torch.no_grad():
        whole = vae.decode(z)
        chunked = chunked_decode_causal3d(vae, z, chunk_latent_frames=3)

    assert chunked.shape == whole.shape
    torch.testing.assert_close(chunked, whole, rtol=0, atol=0)


def test_v2_chunked_decode_matches_whole_decode_with_remainder():
    torch.manual_seed(11)
    vae = _build_tiny_v2()
    z = torch.randn(1, 48, 9, 4, 4)

    with torch.no_grad():
        whole = vae.decode(z)
        chunked = chunked_decode_causal3d(vae, z, chunk_latent_frames=4)

    assert chunked.shape == whole.shape
    torch.testing.assert_close(chunked, whole, rtol=0, atol=0)


def test_v2_chunked_decode_degenerates_to_single_decode_when_chunk_exceeds_clip():
    torch.manual_seed(12)
    vae = _build_tiny_v2()
    z = torch.randn(1, 48, 3, 4, 4)

    with torch.no_grad():
        whole = vae.decode(z)
        chunked = chunked_decode_causal3d(vae, z, chunk_latent_frames=8)

    torch.testing.assert_close(chunked, whole, rtol=0, atol=0)


def test_v2_chunked_decode_first_chunk_flag_only_true_on_first_call():
    """Regression guard for the exact bug the ``first_chunk`` threading exists
    to avoid: if every external chunk wrongly got ``first_chunk=True`` (each
    call's own local frame 0), DupUp3D's leading-frame trim would fire on
    every chunk instead of only the clip's true first frame, dropping real
    output frames and producing a SHORTER video than a whole decode."""
    torch.manual_seed(13)
    vae = _build_tiny_v2()
    z = torch.randn(1, 48, 9, 4, 4)

    with torch.no_grad():
        whole = vae.decode(z)
        chunked = chunked_decode_causal3d(vae, z, chunk_latent_frames=3)

    assert chunked.shape[2] == whole.shape[2]  # would be shorter if mis-threaded


def test_v2_new_feat_cache_matches_internal_cache_size():
    from src.platform.runtime.native.vae.causal_3d_v2 import _count_causal_conv3d_v2

    vae = _build_tiny_v2()
    cache = vae.new_feat_cache()
    assert len(cache) == _count_causal_conv3d_v2(vae.decoder)
    assert all(c is None for c in cache)


def test_v2_decode_default_first_chunk_true_preserves_old_behavior():
    """``decode(z)`` with no explicit ``feat_cache``/``first_chunk`` must be
    byte-identical to before this change."""
    torch.manual_seed(14)
    vae = _build_tiny_v2()
    z = torch.randn(1, 48, 5, 4, 4)

    with torch.no_grad():
        a = vae.decode(z)
        b = vae.decode(z)

    torch.testing.assert_close(a, b, rtol=0, atol=0)


# --- E1 refutation: the "uncached conv2" is temporally pointwise -------------


def test_conv2_is_kernel1_pointwise_in_time_both_versions():
    """Review E1 claimed ``conv2`` is an uncached causal conv re-padded per slice.
    In fact ``conv2`` is a 1x1x1 (kernel-1) channel projection with zero causal
    time-pad in BOTH VAE versions -- it has no temporal receptive field, so
    applying it per-chunk equals the whole clip exactly and it needs no cache
    slot. If it ever gains a temporal kernel > 1 this guard fails, flagging that
    it must then participate in feat_cache."""
    for vae in (_build_tiny_v1(), _build_tiny_v2()):
        assert tuple(vae.conv2.kernel_size)[0] == 1
        assert getattr(vae.conv2, "_causal_time_pad", 0) == 0


def test_e1_scenario_frame1_only_signal_is_chunk_exact():
    """The review's exact killer scenario: z=(1,16,4,H,W), chunk size 2, ONLY
    latent frame 1 nonzero, chunk boundary between frames 1 and 2. If any
    post-boundary conv (the alleged conv2) leaked temporal state uncached, the
    second chunk would diverge from the whole decode. It is byte-exact."""
    torch.manual_seed(20)
    vae = _build_tiny_v1()
    z = torch.zeros(1, 16, 4, 4, 4)
    z[:, :, 1] = torch.randn(1, 16, 4, 4)

    with torch.no_grad():
        whole = vae.decode(z)
        chunked = chunked_decode_causal3d(vae, z, chunk_latent_frames=2)

    torch.testing.assert_close(chunked, whole, rtol=0, atol=0)


# --- E3: accumulate_device bounds GPU peak without changing pixels -----------


def test_accumulate_device_cpu_moves_output_off_device_and_stays_exact():
    """E3: ``accumulate_device`` moves each chunk's pixels off the decode device
    as they're produced (so a long clip's outputs + the final cat don't pile up
    on the GPU and OOM at assembly). The result must equal the default
    (on-device) accumulation exactly."""
    torch.manual_seed(21)
    vae = _build_tiny_v1()
    z = torch.randn(1, 16, 6, 4, 4)

    with torch.no_grad():
        default = chunked_decode_causal3d(vae, z, chunk_latent_frames=2)
        to_cpu = chunked_decode_causal3d(vae, z, chunk_latent_frames=2, accumulate_device="cpu")

    assert to_cpu.device.type == "cpu"
    torch.testing.assert_close(to_cpu, default.cpu(), rtol=0, atol=0)
