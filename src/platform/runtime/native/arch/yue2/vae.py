# Derived from: https://github.com/multimodal-art-projection/YuE src/yue2/modeling_vae.py

"""YuE2 VAE: the Oobleck-style decoder, decode-only (v1 does not port ``OobleckEncoder`` -- the LM lane never needs to re-encode audio)."""

from __future__ import annotations

import math

import torch
import torch.nn as nn

from ...errors import NativeEngineUnsupportedError
from ...vae.minimax_music3_dav import fold_weight_norm_conv

LATENT_DIM = 64
DECODER_CHANNELS = 64
DECODER_C_MULTS: tuple[int, ...] = (1, 2, 4, 8, 16, 32)
DECODER_STRIDES: tuple[int, ...] = (2, 2, 4, 4, 5, 6)
OUT_CHANNELS = 2
SAMPLE_RATE = 48000
HOP_LENGTH = math.prod(DECODER_STRIDES)


class _SnakeBeta(nn.Module):
    """``x + (1/(beta+eps)) * sin(alpha*x)**2``, log-scale alpha/beta (the released config's ``alpha_logscale=True``) so both stay positive: the real alpha/beta used in ``forward`` are ``exp(parameter)``."""

    def __init__(self, channels: int) -> None:
        super().__init__()
        self.alpha = nn.Parameter(torch.zeros(channels))
        self.beta = nn.Parameter(torch.zeros(channels))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        alpha = torch.exp(self.alpha).unsqueeze(0).unsqueeze(-1)
        beta = torch.exp(self.beta).unsqueeze(0).unsqueeze(-1)
        return x + (1.0 / (beta + 1e-9)) * torch.sin(alpha * x).pow(2)


class _ResidualUnit(nn.Module):
    """``.layers`` = SnakeBeta -> Conv1d(k7, dilated, same-padding) -> SnakeBeta -> Conv1d(k1); no cropping (same-padding conv preserves length exactly), residual add straight back."""

    def __init__(self, dim: int, dilation: int) -> None:
        super().__init__()
        padding = (dilation * (7 - 1)) // 2
        self.layers = nn.Sequential(
            _SnakeBeta(dim),
            nn.Conv1d(dim, dim, kernel_size=7, dilation=dilation, padding=padding),
            _SnakeBeta(dim),
            nn.Conv1d(dim, dim, kernel_size=1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.layers(x)


class _DecoderBlock(nn.Module):
    """``.layers`` = SnakeBeta -> ConvTranspose1d(k=2*stride, pad=ceil(stride/2)) -> 3 residual units (dilation 1, 3, 9)."""

    def __init__(self, in_dim: int, out_dim: int, stride: int) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            _SnakeBeta(in_dim),
            nn.ConvTranspose1d(in_dim, out_dim, kernel_size=2 * stride, stride=stride, padding=math.ceil(stride / 2)),
            _ResidualUnit(out_dim, dilation=1),
            _ResidualUnit(out_dim, dilation=3),
            _ResidualUnit(out_dim, dilation=9),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x)


class YuE2VAEDecoder(nn.Module):
    """Decode-only Oobleck decoder."""

    def __init__(
        self,
        *,
        latent_dim: int = LATENT_DIM,
        channels: int = DECODER_CHANNELS,
        c_mults: tuple[int, ...] = DECODER_C_MULTS,
        strides: tuple[int, ...] = DECODER_STRIDES,
        out_channels: int = OUT_CHANNELS,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        super().__init__()
        full_mults = (1, *c_mults)
        depth = len(full_mults)
        if depth - 1 != len(strides):
            raise NativeEngineUnsupportedError(
                f"YuE2 VAE: c_mults ({len(c_mults)}) and strides ({len(strides)}) must be the same length"
            )
        self.latent_dim = latent_dim
        self.sample_rate = sample_rate
        self.hop_length = math.prod(strides)

        blocks: list[nn.Module] = [nn.Conv1d(latent_dim, full_mults[-1] * channels, kernel_size=7, padding=3)]
        for i in range(depth - 1, 0, -1):
            blocks.append(_DecoderBlock(full_mults[i] * channels, full_mults[i - 1] * channels, strides[i - 1]))
        blocks.append(_SnakeBeta(full_mults[0] * channels))
        blocks.append(nn.Conv1d(full_mults[0] * channels, out_channels, kernel_size=7, padding=3, bias=False))
        self.layers = nn.Sequential(*blocks)

    def decode(self, latents: torch.Tensor) -> torch.Tensor:
        """``[B, latent_dim, T]`` fp32 latents -> ``[B, out_channels, N]`` fp32 waveform (no output clipping -- ``final_tanh=False`` in the released config)."""
        if latents.ndim != 3 or latents.shape[1] != self.latent_dim:
            raise NativeEngineUnsupportedError(
                f"YuE2 VAE: expected [batch, {self.latent_dim}, length], got {tuple(latents.shape)}"
            )
        return self.layers(latents.float())


def load(state_dict: dict[str, torch.Tensor], **config_overrides) -> YuE2VAEDecoder:
    """Build a :class:`YuE2VAEDecoder` and strict-load ``decoder.*`` tensors from a raw YuE2 VAE checkpoint state dict."""
    decoder_sd = {
        key[len("decoder."):]: tensor for key, tensor in state_dict.items() if key.startswith("decoder.")
    }
    if not decoder_sd:
        raise NativeEngineUnsupportedError("YuE2 VAE state dict has no 'decoder.*' tensors")
    module = YuE2VAEDecoder(**config_overrides)
    module.load_state_dict(fold_weight_norm_conv(decoder_sd), strict=True)
    return module
