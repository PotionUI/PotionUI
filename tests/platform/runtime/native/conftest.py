"""Shared synthetic state-dict fixtures for native-engine detection tests.

Fixtures use the REAL key names from the local checkpoints with tiny shapes.
Only the shape *ratios* that detection reads matter (e.g. img_in.weight second
dim / patch_size**2 == in_channels), so absolute sizes are shrunk.
"""

from __future__ import annotations

import os
import time

import pytest
import torch


def _fill_blocks(sd: dict, pattern: str, n: int) -> None:
    for i in range(n):
        sd[pattern.format(i) + "dummy.weight"] = torch.zeros(1)


def flux2_sd(hidden: int = 256, depth: int = 8, single: int = 24) -> dict[str, torch.Tensor]:
    """Flux2/Klein signature. axes_dim sum == 128 -> heads == hidden // 128."""
    sd: dict[str, torch.Tensor] = {
        "double_blocks.0.img_attn.norm.key_norm.scale": torch.zeros(16),
        "double_stream_modulation_img.lin.weight": torch.zeros(hidden * 6, hidden),
        "img_in.weight": torch.zeros(hidden, 128),   # in_ch = 128 // 1**2 = 128
        "txt_in.weight": torch.zeros(hidden, 768),   # context_in_dim = 768
    }
    _fill_blocks(sd, "double_blocks.{}.", depth)
    _fill_blocks(sd, "single_blocks.{}.", single)
    return sd


def flux1_sd(hidden: int = 384, depth: int = 19, single: int = 38,
             guidance: bool = True) -> dict[str, torch.Tensor]:
    """Flux1 signature (no double_stream_modulation)."""
    sd: dict[str, torch.Tensor] = {
        "double_blocks.0.img_attn.norm.key_norm.scale": torch.zeros(16),
        "img_in.weight": torch.zeros(hidden, 64),    # in_ch = 64 // 2**2 = 16
        "txt_in.weight": torch.zeros(hidden, 512),
    }
    if guidance:
        sd["guidance_in.in_layer.weight"] = torch.zeros(1)
    _fill_blocks(sd, "double_blocks.{}.", depth)
    _fill_blocks(sd, "single_blocks.{}.", single)
    return sd


def clip_l_sd(hidden: int = 64, layers: int = 12) -> dict[str, torch.Tensor]:
    sd = {"text_model.embeddings.token_embedding.weight": torch.zeros(49408, hidden)}
    _fill_blocks(sd, "text_model.encoder.layers.{}.", layers)
    return sd


def t5xxl_sd(hidden: int = 64, blocks: int = 24, scaled_fp8: bool = False) -> dict[str, torch.Tensor]:
    sd = {
        "shared.weight": torch.zeros(32128, hidden),
        "encoder.block.0.layer.0.SelfAttention.q.weight": torch.zeros(hidden, hidden),
    }
    _fill_blocks(sd, "encoder.block.{}.", blocks)
    if scaled_fp8:
        sd["scaled_fp8"] = torch.zeros(0)
    return sd


def qwen3_sd(hidden: int, layers: int = 36) -> dict[str, torch.Tensor]:
    sd = {
        "model.embed_tokens.weight": torch.zeros(151936, hidden),
        "model.layers.0.self_attn.q_norm.weight": torch.zeros(128),
    }
    _fill_blocks(sd, "model.layers.{}.", layers)
    return sd


def flux_ae_sd(latent: int = 16) -> dict[str, torch.Tensor]:
    return {
        "encoder.conv_in.weight": torch.zeros(128, 3, 3, 3),
        "encoder.conv_out.weight": torch.zeros(latent * 2, 512, 3, 3),
        "decoder.conv_in.weight": torch.zeros(512, latent, 3, 3),
        "decoder.conv_out.weight": torch.zeros(3, 128, 3, 3),
        "decoder.mid.attn_1.k.weight": torch.zeros(1),
    }


def flux2_ae_sd(latent: int = 32) -> dict[str, torch.Tensor]:
    return {
        "encoder.conv_in.weight": torch.zeros(128, 3, 3, 3),
        "encoder.conv_out.weight": torch.zeros(latent * 2, 512, 3, 3),
        "decoder.conv_in.weight": torch.zeros(512, latent, 3, 3),
        "decoder.conv_out.weight": torch.zeros(3, 128, 3, 3),
        "quant_conv.weight": torch.zeros(latent * 2, latent * 2, 1, 1),
        "post_quant_conv.weight": torch.zeros(latent, latent, 1, 1),
        "bn.running_mean": torch.zeros(128),
        "decoder.mid_block.attentions.0.to_k.weight": torch.zeros(1),
    }


def causal3d_vae_sd(latent: int = 16, wan22: bool = False) -> dict[str, torch.Tensor]:
    """Minimal Wan-2.1-shaped causal 3D VAE signature (Qwen-Image's VAE shape).

    Only the keys ``detect_causal3d_vae_config`` actually reads. ``wan22=True``
    adds the nested key that must exclude this from causal3d detection (the
    Wan 2.2 variant, not handled by this detector).
    """
    sd: dict[str, torch.Tensor] = {
        "decoder.middle.0.residual.0.gamma": torch.zeros(1, 1, 1, 1),
        "encoder.conv1.weight": torch.zeros(96, 3, 3, 3, 3),
        "decoder.head.2.weight": torch.zeros(3, 96, 3, 3, 3),
        "conv2.weight": torch.zeros(latent, latent, 1, 1, 1),
    }
    if wan22:
        sd["decoder.upsamples.0.upsamples.0.residual.2.weight"] = torch.zeros(1)
    return sd


def causal3d_v2_vae_sd(latent: int = 48) -> dict[str, torch.Tensor]:
    """Minimal Wan-2.2-shaped causal 3D VAE signature (patchified: encoder/
    decoder conv channels are image_ch * patch_size**2 = 3*4 = 12)."""
    return {
        "decoder.middle.0.residual.0.gamma": torch.zeros(1, 1, 1, 1),
        "decoder.upsamples.0.upsamples.0.residual.2.weight": torch.zeros(1),
        "encoder.conv1.weight": torch.zeros(160, 12, 3, 3, 3),
        "decoder.head.2.weight": torch.zeros(12, 256, 3, 3, 3),
        "conv2.weight": torch.zeros(latent, latent, 1, 1, 1),
    }


def seedvr2_dit_sd(
    vid_dim: int = 64, heads: int = 4, head_dim: int = 16, num_layers: int = 4,
    mm_layers: int = 2, vid_in_channels: int = 33, vid_out_channels: int = 16,
    txt_in_dim: int = 5120, emb_dim: int | None = None, mlp_hidden: int = 96,
    variant: str = "3b",
) -> dict[str, torch.Tensor]:
    """Minimal SeedVR2 NaDiT signature -- only the keys ``_detect_seedvr2`` reads.

    ``vid_in``/``vid_out`` fold ``patch_t*patch_h*patch_w == 4`` voxels into the
    channel dim (so raw cols/rows are ``channels * 4``). The first ``mm_layers``
    blocks carry split ``.vid``/``.txt`` weights (block 0 must be one, since the
    detector probes ``blocks.0.*.vid``); later blocks share a single ``.all`` set
    -- both counted by ``count_blocks`` but only ``.vid`` blocks count as mm.

    ``variant`` toggles the 3B-vs-7B discriminator keys: the 3B carries a SwiGLU
    ``proj_in_gate`` (and a ``vid_out_norm`` head), the 7B has neither.
    """
    pack = 4
    if emb_dim is None:
        emb_dim = 6 * vid_dim
    sd: dict[str, torch.Tensor] = {
        "vid_in.proj.weight": torch.zeros(vid_dim, vid_in_channels * pack),
        "vid_out.proj.weight": torch.zeros(vid_out_channels * pack, vid_dim),
        "txt_in.weight": torch.zeros(vid_dim, txt_in_dim),
        "emb_in.proj_out.weight": torch.zeros(emb_dim, vid_dim),
    }
    if variant == "3b":
        sd["vid_out_norm.weight"] = torch.zeros(vid_dim)
    for i in range(num_layers):
        tag = "vid" if i < mm_layers else "all"
        sd[f"blocks.{i}.ada.{tag}.attn_shift"] = torch.zeros(vid_dim)
        sd[f"blocks.{i}.attn.norm_q.{tag}.weight"] = torch.zeros(head_dim)
        sd[f"blocks.{i}.attn.proj_qkv.{tag}.weight"] = torch.zeros(heads * head_dim * 3, vid_dim)
        sd[f"blocks.{i}.mlp.{tag}.proj_in.weight"] = torch.zeros(mlp_hidden, vid_dim)
        if variant == "3b":
            sd[f"blocks.{i}.mlp.{tag}.proj_in_gate.weight"] = torch.zeros(mlp_hidden, vid_dim)
    return sd


def seedvr2_vae_sd(latent: int = 16) -> dict[str, torch.Tensor]:
    """Minimal SeedVR2 causal-video VAE signature: 5D (inflated 3D) conv weights,
    no quant_conv/post_quant_conv, no Wan bottleneck gamma."""
    return {
        "encoder.conv_in.weight": torch.zeros(128, 3, 3, 3, 3),
        "encoder.conv_out.weight": torch.zeros(latent * 2, 512, 3, 3, 3),
        "decoder.conv_in.weight": torch.zeros(512, latent, 3, 3, 3),
        "decoder.conv_out.weight": torch.zeros(3, 128, 3, 3, 3),
    }


def minimax_h3_video_vae_sd(latent: int = 24, decoder_dim: int = 128, num_layers: int = 2) -> dict[str, torch.Tensor]:
    """Minimal MiniMax-H3 video VAE (Comfy-Org repack) signature -- the real
    key names from ``ai/minimax_h3/video_vae_header.json``, shrunk shapes.
    ``decoder_dim``/``num_layers`` are small so ``decoder_num_attention_heads
    * decoder_attention_head_dim`` (32*64=2048 in the real checkpoint) doesn't
    need to hold at this tiny scale -- the detector recomputes head_dim from
    the observed dim when it doesn't match the fixed head count."""
    sd: dict[str, torch.Tensor] = {
        "encoder.conv_in.weight": torch.zeros(8, 3, 3, 3, 3),
        "decoder.mask_token": torch.zeros(1, 1, decoder_dim),
        "decoder.register_tokens": torch.zeros(1, 4, decoder_dim),
        "decoder.x_embedder.weight": torch.zeros(decoder_dim, latent),
        "post_quant_conv.weight": torch.zeros(latent, latent, 1, 1, 1),
    }
    for i in range(num_layers):
        sd[f"decoder.transformer_blocks.{i}.norm1.weight"] = torch.zeros(decoder_dim)
    return sd


def minimax_h3_audio_vae_sd(latent_channels: int = 32, latent_dim: int = 64, encoder_dim: int = 8) -> dict[str, torch.Tensor]:
    """Minimal MiniMax-H3 audio VAE (Comfy-Org repack) signature -- the real
    key names from ``ai/minimax_h3/audio_vae_header.json``, shrunk shapes."""
    return {
        "pre_block.attn.qkv.weight": torch.zeros(latent_dim * 3, latent_dim),
        "dec_in_proj.weight": torch.zeros(latent_dim, latent_channels, 1),
        "decoder.conv_pre.weight": torch.zeros(16, latent_dim, 7),
        "mean_proj.weight": torch.zeros(latent_channels, latent_channels, 1),
        "encoder.block.0.weight": torch.zeros(encoder_dim, 1, 7),
    }


# --- bf16_cpu_heavy: deterministic skip for the runner-CPU bf16 roulette ---
#
# Part of GitHub's hosted runner fleet lacks native bf16 (no AMX/AVX512-BF16);
# torch's bf16 conv2d fallback there is 10-20x slower, and a full sample+decode
# at real (if tiny) tensor sizes then blows through even a 300s per-test
# timeout inside the VAE's conv2d layers. Tests marked ``bf16_cpu_heavy`` are
# skipped on such a runner instead of stalling -- see docs/testing-notes.md.

_FORCE_ENV = "POTIONUI_FORCE_BF16_PROBE"

# Workload: (1,128,192,192) x (128,128,3,3) conv2d, the shape of a mid-decode
# VAE layer. Measured on this dev container (11th-gen Intel, no
# avx512_bf16/amx_bf16 cpuid flag) at a stable ~41ms/call (<5% spread over 10
# reps) -- i.e. this box's fallback is *not* the catastrophic one, so it
# reads as "native-ish" against the threshold below. A runner hitting the
# reported 10-20x-slower fallback would land at ~410-820ms/call for the same
# workload. 150ms sits ~3.6x above this container's measured time and
# ~2.7-5.4x below the 10-20x-fallback range -- wide margin on both sides so
# per-run jitter can't flip the classification.
_THRESHOLD_MS = 150.0
_WARMUP_ITERS = 1
_TIMED_ITERS = 2
_HARD_CAP_S = 5.0

_cached_result: bool | None = None


def _probe_once() -> float:
    x = torch.randn(1, 128, 192, 192, dtype=torch.bfloat16)
    w = torch.randn(128, 128, 3, 3, dtype=torch.bfloat16)
    t0 = time.perf_counter()
    torch.nn.functional.conv2d(x, w, padding=1)
    return (time.perf_counter() - t0) * 1000.0


def cpu_bf16_is_usable() -> bool:
    """True when this CPU runs bf16 conv2d at native (not 10-20x-fallback)
    speed. Measured once per process and cached.

    ``POTIONUI_FORCE_BF16_PROBE=pass|fail`` overrides the measurement so the
    skip mechanism itself can be exercised without a slow-bf16 CPU on hand
    (see docs/testing-notes.md).
    """
    global _cached_result

    override = os.environ.get(_FORCE_ENV)
    if override == "pass":
        return True
    if override == "fail":
        return False

    if _cached_result is not None:
        return _cached_result

    deadline = time.perf_counter() + _HARD_CAP_S
    for _ in range(_WARMUP_ITERS):
        _probe_once()
        if time.perf_counter() > deadline:
            _cached_result = False
            return _cached_result

    samples = [_probe_once() for _ in range(_TIMED_ITERS)]
    _cached_result = (sum(samples) / len(samples)) < _THRESHOLD_MS
    return _cached_result


def pytest_collection_modifyitems(config, items):
    """Skip ``bf16_cpu_heavy`` tests on a CPU that can't run bf16 at native
    speed. A GPU host runs these tests on the fast path regardless of what
    the CPU probe would say, so the skip only applies when no CUDA device is
    present."""
    skip_bf16_heavy = pytest.mark.skip(
        reason="runner CPU lacks native bf16; bf16 e2e would time out (deterministic skip, see testing-notes)"
    )
    for item in items:
        if "bf16_cpu_heavy" not in item.keywords:
            continue
        if torch.cuda.is_available():
            continue
        if not cpu_bf16_is_usable():
            item.add_marker(skip_bf16_heavy)
