# Derived from: diffusers `autoencoder_kl_minimax_h3_audio.py` (Apache-2.0,
# "Copyright 2025 The MiniMax authors and The HuggingFace Team") -- the DAC
# encoder, causal-attention bottleneck projection, and BigVGAN decoder are
# ported from that file. Module/attribute names match the Comfy-Org repack
# (`minimax_h3_audio_vae_fp32.safetensors`) -- verified against the repack's
# own safetensors header (`ai/minimax_h3/audio_vae_header.json`, 917 keys),
# which matches diffusers' own naming almost exactly EXCEPT for one point
# (see "weight_norm" below).
"""MiniMax-H3 audio VAE: DAC-lineage encoder + BigVGAN decoder, waveform in /
waveform out (no mel front-end, no separate vocoder).

**Discrepancy vs. the expected weight_norm spelling.** diffusers' reference
wraps every conv in this file with `torch.nn.utils.weight_norm` (the
`weight_g`/`weight_v` two-parameter spelling). The REAL Comfy-Org repack
checkpoint carries **plain `weight`/`bias` keys with no `weight_g`/`weight_v`
anywhere** (verified: zero keys matching `*weight_g*`/`*weight_v*`/
`*parametriz*` in the 917-key header) -- the repack was exported with
`remove_weight_norm()` already applied (a normal step for an
inference-only single-file release: weight-normed conv weights collapse to
one ordinary tensor once training is over). This module therefore builds
EVERY conv as a plain `operations.Conv1d`/`ConvTranspose1d` with no
weight_norm wrapper at all -- reparametrizing at load would look for
`weight_g`/`weight_v` keys that don't exist and fail to load the real
`weight` tensor. Module/attribute structure otherwise matches diffusers 1:1
(same `block`-nested `nn.Sequential` naming throughout the DAC encoder, same
`pre_block`/`mean_proj`/`logs_proj`/`dec_in_proj`/`decoder` top-level names).

**Precision -- fp32 ALWAYS, not just at load.** The repack file is fp32
(unlike the video VAE's fp16 repack), and bf16 compute measurably degrades
this decoder (~20dB quieter, per the dossier). `NativeEngineLoader._ops_for`
already selects `manual_cast` for this checkpoint (storage fp32 != compute
bf16), but `manual_cast`'s `cast_bias_weight` casts a module's WEIGHT to
match the ACTIVATION's dtype -- the OPPOSITE of what "always fp32" needs (a
bf16 activation would downcast this fp32 weight to bf16, not the reverse).
`encode`/`decode` therefore force the INPUT to fp32 at their own entry
point (`sample.float()` / `latents.float()`); every downstream `operations`-
built layer's `cast_bias_weight` then sees an fp32 activation and casts ITS
(already-fp32-stored) weight to match, keeping the entire chain in fp32 with
no per-submodule casting needed. This is the same "cast follows the
activation, not the weight" mechanism the video VAE explicitly does NOT need
(it just runs whatever precision `manual_cast` naturally selects).

**Posterior.** MiniMax-H3 always consumes the posterior MEAN
(`latent_dist.mode()`) -- `logs_proj` is a real checkpoint weight (built here
for key-parity) that the reference pipeline never evaluates; `encode` never
calls it either, matching both the reference's documented behavior and this
package's house convention of returning the deterministic mode directly (no
separate distribution class -- see `causal_3d.py`/`ae_2d.py`).

**Kaiser-sinc anti-alias filter buffers are PERSISTENT** (`Activation1d`'s
`upsample.filter` / `downsample.lowpass.filter`) -- loaded from the
checkpoint, not recomputed. `post_load` is therefore a documented no-op.

**Mono only.** `encode` asserts `sample.shape == (B, 1, samples)`; stereo is
carried as two batch items by the caller (matches the video family's own
`min​imax_h3_video.py`-adjacent pipe contract, out of scope here).
"""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule
from ..errors import NativeEngineUnsupportedError

# -- fixed H3 audio-VAE geometry (single released variant) ------------------

ENCODER_DIM = 64
ENCODER_RATES: tuple[int, ...] = (2, 4, 4, 5, 5)
LATENT_DIM = 2048
LATENT_CHANNELS = 32
NUM_ATTENTION_HEADS = 8
DECODER_DIM = 1024
DECODER_RATES: tuple[int, ...] = (5, 5, 2, 2, 2, 2, 2)
DECODER_KERNEL_SIZES: tuple[int, ...] = (9, 9, 4, 4, 4, 4, 4)
RESBLOCK_KERNEL_SIZES: tuple[int, ...] = (3, 7, 11)
RESBLOCK_DILATION_SIZES: tuple[tuple[int, ...], ...] = ((1, 3, 5), (1, 3, 5), (1, 3, 5))
SAMPLE_RATE = 32000
HOP_LENGTH = math.prod(ENCODER_RATES)  # 800 -> 40 latents/s at 32kHz


# -- Snake / SnakeBeta (no matmul, small elementwise params -- not built
#    through `operations`, matching e.g. causal_3d.py's RMS_norm) ----------

class _Snake1d(nn.Module):
    def __init__(self, channels: int) -> None:
        super().__init__()
        self.alpha = nn.Parameter(torch.ones(1, channels, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + (self.alpha + 1e-9).reciprocal() * torch.sin(self.alpha * x).pow(2)


class _SnakeBeta(nn.Module):
    def __init__(self, channels: int) -> None:
        super().__init__()
        self.alpha = nn.Parameter(torch.zeros(channels))
        self.beta = nn.Parameter(torch.zeros(channels))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        alpha = torch.exp(self.alpha).unsqueeze(0).unsqueeze(-1)
        beta = torch.exp(self.beta).unsqueeze(0).unsqueeze(-1)
        return x + (beta + 1e-9).reciprocal() * torch.sin(alpha * x).pow(2)


# -- Kaiser-sinc anti-alias filters (persistent buffers) ---------------------

def _kaiser_sinc_filter1d(cutoff: float, half_width: float, kernel_size: int) -> torch.Tensor:
    half_size = kernel_size // 2
    attenuation = 2.285 * (half_size - 1) * math.pi * (4 * half_width) + 7.95
    if attenuation > 50.0:
        beta = 0.1102 * (attenuation - 8.7)
    elif attenuation >= 21.0:
        beta = 0.5842 * (attenuation - 21) ** 0.4 + 0.07886 * (attenuation - 21.0)
    else:
        beta = 0.0
    window = torch.kaiser_window(kernel_size, beta=beta, periodic=False)
    if kernel_size % 2 == 0:
        time = torch.arange(-half_size, half_size) + 0.5
    else:
        time = torch.arange(kernel_size) - half_size
    filt = 2 * cutoff * window * torch.sinc(2 * cutoff * time)
    filt = filt / filt.sum()
    return filt.view(1, 1, kernel_size)


class _LowPassFilter1d(nn.Module):
    def __init__(self, cutoff: float, half_width: float, stride: int, kernel_size: int) -> None:
        super().__init__()
        even = kernel_size % 2 == 0
        self.pad_left = kernel_size // 2 - int(even)
        self.pad_right = kernel_size // 2
        self.stride = stride
        self.register_buffer("filter", _kaiser_sinc_filter1d(cutoff, half_width, kernel_size), persistent=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        c = x.shape[1]
        x = F.pad(x, (self.pad_left, self.pad_right), mode="replicate")
        return F.conv1d(x, self.filter.expand(c, -1, -1).to(x), stride=self.stride, groups=c)


class _UpSample1d(nn.Module):
    def __init__(self, ratio: int, kernel_size: int) -> None:
        super().__init__()
        self.ratio = ratio
        self.stride = ratio
        self.pad = kernel_size // ratio - 1
        self.pad_left = self.pad * self.stride + (kernel_size - self.stride) // 2
        self.pad_right = self.pad * self.stride + (kernel_size - self.stride + 1) // 2
        self.register_buffer(
            "filter", _kaiser_sinc_filter1d(cutoff=0.5 / ratio, half_width=0.6 / ratio, kernel_size=kernel_size),
            persistent=True,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        c = x.shape[1]
        x = F.pad(x, (self.pad, self.pad), mode="replicate")
        x = self.ratio * F.conv_transpose1d(x, self.filter.expand(c, -1, -1).to(x), stride=self.stride, groups=c)
        return x[..., self.pad_left : -self.pad_right]


class _DownSample1d(nn.Module):
    def __init__(self, ratio: int, kernel_size: int) -> None:
        super().__init__()
        self.lowpass = _LowPassFilter1d(cutoff=0.5 / ratio, half_width=0.6 / ratio, stride=ratio, kernel_size=kernel_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.lowpass(x)


class _Activation1d(nn.Module):
    def __init__(self, activation: nn.Module, ratio: int = 2, kernel_size: int = 12) -> None:
        super().__init__()
        self.act = activation
        self.upsample = _UpSample1d(ratio, kernel_size)
        self.downsample = _DownSample1d(ratio, kernel_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.upsample(x)
        x = self.act(x)
        return self.downsample(x)


# -- DAC encoder --------------------------------------------------------

class _AudioResidualUnit(nn.Module):
    def __init__(self, dim: int, dilation: int, *, operations: Any) -> None:
        super().__init__()
        pad = ((7 - 1) * dilation) // 2
        self.block = nn.Sequential(
            _Snake1d(dim),
            operations.Conv1d(dim, dim, kernel_size=7, dilation=dilation, padding=pad),
            _Snake1d(dim),
            operations.Conv1d(dim, dim, kernel_size=1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = self.block(x)
        pad = (x.shape[-1] - residual.shape[-1]) // 2
        if pad > 0:
            x = x[..., pad:-pad]
        return x + residual


class _AudioEncoderBlock(nn.Module):
    def __init__(self, dim: int, stride: int, *, operations: Any) -> None:
        super().__init__()
        self.block = nn.Sequential(
            _AudioResidualUnit(dim // 2, dilation=1, operations=operations),
            _AudioResidualUnit(dim // 2, dilation=3, operations=operations),
            _AudioResidualUnit(dim // 2, dilation=9, operations=operations),
            _Snake1d(dim // 2),
            operations.Conv1d(dim // 2, dim, kernel_size=2 * stride, stride=stride, padding=math.ceil(stride / 2)),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


class _AudioEncoder(nn.Module):
    """`[B, 1, samples] -> [B, latent_dim, samples / hop_length]`. Flat
    `block.{0..7}` `nn.Sequential` naming matches the checkpoint 1:1."""

    def __init__(self, d_model: int, strides: tuple[int, ...], d_latent: int, *, operations: Any) -> None:
        super().__init__()
        block: list[nn.Module] = [operations.Conv1d(1, d_model, kernel_size=7, padding=3)]
        for stride in strides:
            d_model *= 2
            block.append(_AudioEncoderBlock(d_model, stride=stride, operations=operations))
        block += [_Snake1d(d_model), operations.Conv1d(d_model, d_latent, kernel_size=3, padding=1)]
        self.block = nn.Sequential(*block)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


# -- causal-attention bottleneck projection (`pre_block`) --------------------

class _AudioGeGluMlp(nn.Module):
    def __init__(self, in_features: int, hidden_features: int, *, operations: Any) -> None:
        super().__init__()
        self.norm = operations.LayerNorm(in_features)
        self.w0 = operations.Linear(in_features, hidden_features)
        self.w1 = operations.Linear(in_features, hidden_features)
        self.w2 = operations.Linear(hidden_features, in_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.norm(x)
        return self.w2(F.gelu(self.w0(x), approximate="tanh") * self.w1(x))


class _AudioCausalAttention(nn.Module):
    """Causal self-attention that narrows `in_dim` -> `out_dim`: fused
    bias-less `qkv` Linear + separate `q_bias`/`v_bias` parameters + a frozen
    `zero_k_bias` buffer (all real checkpoint keys, exactly as stored).
    Heads are mean-pooled away (not concatenated); the remaining `head_dim`
    axis is adaptive-avg-pooled down to `out_dim`."""

    def __init__(self, in_dim: int, out_dim: int, num_heads: int, *, operations: Any) -> None:
        super().__init__()
        self.out_dim = out_dim
        self.num_heads = num_heads
        self.head_dim = in_dim // num_heads
        self.qkv = operations.Linear(in_dim, in_dim * 3, bias=False)
        self.q_bias = nn.Parameter(torch.zeros(in_dim))
        self.v_bias = nn.Parameter(torch.zeros(in_dim))
        self.register_buffer("zero_k_bias", torch.zeros(in_dim), persistent=True)
        self.proj = operations.Linear(out_dim, out_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, n, _ = x.shape
        bias = torch.cat((self.q_bias, self.zero_k_bias, self.v_bias)).to(x.dtype)
        qkv = F.linear(x, self.qkv.weight.to(x.dtype), bias)
        qkv = qkv.reshape(b, n, 3, self.num_heads, self.head_dim)
        query, key, value = qkv.permute(2, 0, 3, 1, 4).unbind(0)  # each (b, heads, n, head_dim)

        out = F.scaled_dot_product_attention(query, key, value, attn_mask=None, is_causal=True)
        out = out.transpose(1, 2)                       # (b, n, heads, head_dim)
        out = torch.mean(out, dim=2)                     # heads mean-pooled -> (b, n, head_dim)
        out = F.adaptive_avg_pool1d(out, self.out_dim)    # head_dim -> out_dim (last axis pooled)
        return self.proj(out)


class _AudioAttnProjection(nn.Module):
    """`pre_block`: residual causal-attention + GeGLU, rewiring `latent_dim`
    (the encoder trunk width) -> `latent_channels` (the diffusion latent)."""

    def __init__(self, in_dim: int, out_dim: int, num_heads: int, *, mlp_ratio: int = 2, operations: Any) -> None:
        super().__init__()
        self.norm1 = operations.LayerNorm(in_dim)
        self.attn = _AudioCausalAttention(in_dim, out_dim, num_heads, operations=operations)
        self.proj = operations.Linear(in_dim, out_dim)
        self.norm3 = operations.LayerNorm(in_dim)
        self.norm2 = operations.LayerNorm(out_dim)
        self.mlp = _AudioGeGluMlp(out_dim, out_dim * mlp_ratio, operations=operations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.proj(self.norm3(x)) + self.attn(self.norm1(x))
        return x + self.mlp(self.norm2(x))


# -- BigVGAN decoder --------------------------------------------------------

class _AudioAMPBlock(nn.Module):
    def __init__(self, channels: int, kernel_size: int, dilation: tuple[int, ...], *, operations: Any) -> None:
        super().__init__()
        self.convs1 = nn.ModuleList([
            operations.Conv1d(channels, channels, kernel_size, dilation=d, padding=(kernel_size * d - d) // 2)
            for d in dilation
        ])
        self.convs2 = nn.ModuleList([
            operations.Conv1d(channels, channels, kernel_size, dilation=1, padding=(kernel_size - 1) // 2)
            for _ in dilation
        ])
        self.activations = nn.ModuleList([_Activation1d(_SnakeBeta(channels)) for _ in range(2 * len(dilation))])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        acts1, acts2 = self.activations[::2], self.activations[1::2]
        for conv1, conv2, act1, act2 in zip(self.convs1, self.convs2, acts1, acts2):
            residual = conv1(act1(x))
            residual = conv2(act2(residual))
            x = residual + x
        return x


class _AudioBigVGANDecoder(nn.Module):
    """`[B, latent_dim, num_frames] -> [B, 1, num_frames * hop_length]`.
    `ups.{i}` is a one-element `ModuleList` (matches the checkpoint's
    `ups.{i}.0.*` keys)."""

    def __init__(
        self, *, in_channels: int, upsample_initial_channel: int, upsample_rates: tuple[int, ...],
        upsample_kernel_sizes: tuple[int, ...], resblock_kernel_sizes: tuple[int, ...],
        resblock_dilation_sizes: tuple[tuple[int, ...], ...], operations: Any,
    ) -> None:
        super().__init__()
        self.num_kernels = len(resblock_kernel_sizes)
        self.num_upsamples = len(upsample_rates)

        self.conv_pre = operations.Conv1d(in_channels, upsample_initial_channel, 7, 1, padding=3)

        self.ups = nn.ModuleList()
        for i, (rate, kernel) in enumerate(zip(upsample_rates, upsample_kernel_sizes)):
            self.ups.append(nn.ModuleList([
                operations.ConvTranspose1d(
                    upsample_initial_channel // (2 ** i), upsample_initial_channel // (2 ** (i + 1)),
                    kernel, rate, padding=(kernel - rate) // 2,
                )
            ]))

        self.resblocks = nn.ModuleList()
        for i in range(self.num_upsamples):
            channels = upsample_initial_channel // (2 ** (i + 1))
            for kernel, dilation in zip(resblock_kernel_sizes, resblock_dilation_sizes):
                self.resblocks.append(_AudioAMPBlock(channels, kernel, tuple(dilation), operations=operations))

        self.activation_post = _Activation1d(_SnakeBeta(channels))
        self.conv_post = operations.Conv1d(channels, 1, 7, 1, padding=3, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.conv_pre(x)
        for i in range(self.num_upsamples):
            x = self.ups[i][0](x)
            residual = None
            for j in range(self.num_kernels):
                block = self.resblocks[i * self.num_kernels + j](x)
                residual = block if residual is None else residual + block
            x = residual / self.num_kernels
        x = self.activation_post(x)
        x = self.conv_post(x)
        return torch.clamp(x, min=-1.0, max=1.0)


class MiniMaxH3AudioVAE(NativeArchModule):
    def __init__(
        self, *,
        encoder_dim: int = ENCODER_DIM,
        encoder_rates: tuple[int, ...] = ENCODER_RATES,
        latent_dim: int = LATENT_DIM,
        latent_channels: int = LATENT_CHANNELS,
        num_attention_heads: int = NUM_ATTENTION_HEADS,
        decoder_dim: int = DECODER_DIM,
        decoder_rates: tuple[int, ...] = DECODER_RATES,
        decoder_kernel_sizes: tuple[int, ...] = DECODER_KERNEL_SIZES,
        resblock_kernel_sizes: tuple[int, ...] = RESBLOCK_KERNEL_SIZES,
        resblock_dilation_sizes: tuple[tuple[int, ...], ...] = RESBLOCK_DILATION_SIZES,
        sample_rate: int = SAMPLE_RATE,
        operations: Any,
    ) -> None:
        super().__init__()
        encoder_rates = tuple(int(r) for r in encoder_rates)
        decoder_rates = tuple(int(r) for r in decoder_rates)
        self.hop_length = math.prod(encoder_rates)
        self.sample_rate = sample_rate

        self.encoder = _AudioEncoder(encoder_dim, encoder_rates, latent_dim, operations=operations)
        self.pre_block = _AudioAttnProjection(latent_dim, latent_channels, num_attention_heads, operations=operations)
        self.mean_proj = operations.Conv1d(latent_channels, latent_channels, 1)
        # Built for key parity -- the reference pipeline never evaluates this
        # head (see module docstring "Posterior").
        self.logs_proj = operations.Conv1d(latent_channels, latent_channels, 1)

        self.dec_in_proj = operations.Conv1d(latent_channels, latent_dim, 1)
        self.decoder = _AudioBigVGANDecoder(
            in_channels=latent_dim, upsample_initial_channel=decoder_dim,
            upsample_rates=decoder_rates, upsample_kernel_sizes=tuple(int(k) for k in decoder_kernel_sizes),
            resblock_kernel_sizes=tuple(int(k) for k in resblock_kernel_sizes),
            resblock_dilation_sizes=tuple(tuple(int(d) for d in dilation) for dilation in resblock_dilation_sizes),
            operations=operations,
        )

        self.register_buffer("latents_mean", torch.zeros(latent_channels), persistent=True)
        self.register_buffer("latents_std", torch.ones(latent_channels), persistent=True)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "MiniMaxH3AudioVAE":
        return cls(
            encoder_dim=config.get("encoder_dim", ENCODER_DIM),
            encoder_rates=tuple(config.get("encoder_rates", ENCODER_RATES)),
            latent_dim=config.get("latent_dim", LATENT_DIM),
            latent_channels=config.get("latent_channels", LATENT_CHANNELS),
            num_attention_heads=config.get("num_attention_heads", NUM_ATTENTION_HEADS),
            decoder_dim=config.get("decoder_dim", DECODER_DIM),
            decoder_rates=tuple(config.get("decoder_rates", DECODER_RATES)),
            decoder_kernel_sizes=tuple(config.get("decoder_kernel_sizes", DECODER_KERNEL_SIZES)),
            resblock_kernel_sizes=tuple(config.get("resblock_kernel_sizes", RESBLOCK_KERNEL_SIZES)),
            resblock_dilation_sizes=tuple(
                tuple(d) for d in config.get("resblock_dilation_sizes", RESBLOCK_DILATION_SIZES)
            ),
            sample_rate=config.get("sample_rate", SAMPLE_RATE),
            operations=operations,
        )

    def post_load(self) -> None:
        # Kaiser-sinc filters are LOADED (persistent buffers), not derived --
        # nothing to recompute.
        return None

    def encode(self, sample: torch.Tensor) -> torch.Tensor:
        """Mono waveform `(B, 1, samples)` -> latent mean `(B, latent_channels,
        samples / hop_length)`. Right-pads to a `hop_length` multiple first."""
        if sample.ndim != 3 or sample.shape[1] != 1:
            raise NativeEngineUnsupportedError(
                f"MiniMax-H3 audio VAE: expected mono [batch, 1, samples], got {tuple(sample.shape)}"
            )
        sample = sample.float()  # fp32 always -- see module docstring.
        right_pad = math.ceil(sample.shape[-1] / self.hop_length) * self.hop_length - sample.shape[-1]
        if right_pad > 0:
            sample = F.pad(sample, (0, right_pad))

        hidden = self.encoder(sample)
        hidden = self.pre_block(hidden.transpose(1, 2)).transpose(1, 2)
        return self.mean_proj(hidden)

    def decode(self, latents: torch.Tensor) -> torch.Tensor:
        """Latents `(B, latent_channels, num_frames)` -> waveform `(B, 1,
        num_frames * hop_length)`, clamped to `[-1, 1]`."""
        if latents.ndim != 3:
            raise NativeEngineUnsupportedError(
                f"MiniMax-H3 audio VAE: expected [batch, latent_channels, num_frames], got {tuple(latents.shape)}"
            )
        latents = latents.float()  # fp32 always -- see module docstring.
        hidden = self.dec_in_proj(latents)
        return self.decoder(hidden)
