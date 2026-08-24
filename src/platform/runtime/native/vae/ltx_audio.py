"""LTX-2/2.3 audio VAE + vocoder -- **decode-only**.

Two independent, separately-loadable arch modules live here, both vendored
from ComfyUI's ``comfy/ldm/lightricks/vae/causal_audio_autoencoder.py`` and
``comfy/ldm/lightricks/vocoders/vocoder.py``:

  * ``LTXAudioAutoencoder`` -- a standard causal-2D-conv VAE over mel-spectrograms
    treated as a ``[batch, channels, time, mel_bins]`` "image". Real key prefix:
    ``audio_vae.*`` (either bare or inside an all-in-one checkpoint).
  * ``LTXVocoder`` -- a HiFi-GAN-v1-style vocoder (mel -> waveform). Real key
    prefix: ``vocoder.*``.

**CausalityAxis**: unlike the video VAE's temporal-only causal padding, this
2D VAE can make EITHER spatial axis causal (left-pad only) via
``CausalConv2d``. The real checkpoints use ``causality_axis="height"`` --
height here is the *mel_bins* axis is NOT what's padded; per
``AudioPreprocessor.waveform_to_mel``, mel spectrograms are permuted to
``[batch, channels, time, mel_bins]`` (``mel.permute(0, 1, 3, 2)``), i.e.
dim=2 is TIME and dim=3 is mel_bins. ``CausalConv2d``'s padding tuple is
``(pad_left, pad_right, pad_top, pad_bottom)`` in ``F.pad`` order, which pads
``(dim=-1, dim=-2)`` = ``(mel_bins, time)``. ``CausalityAxis.HEIGHT`` pads only
``dim=-2`` (time) causally and leaves mel_bins symmetric -- so despite the
name, "height" causality means TIME is the causal axis, matching audio's
"can't see the future" requirement. Verified against both local audio VAE
files' embedded config (``causality_axis: "height"`` in both).

**Decode-only scope**: both ``encoder.*`` and ``decoder.*`` submodules are
constructed (real checkpoints ship both -- verified via header dump of
``LTX2_audio_vae_bf16.safetensors``, 102 ``audio_vae.*`` tensors covering
both), so ``load_into_module``'s key-parity gate gets exact 1:1 matching with
no allowlist needed. Only ``decode()`` is exposed/tested; ``Encoder.forward``
is intentionally not ported, since nothing in this codebase calls it.

**Vocoder config drift (LTX2 vs LTX23) -- LTX23 fully vendored (main + bwe)**:
LTX2's embedded ``vocoder`` config is the flat
HiFi-GAN-v1 shape ComfyUI's local ``comfy/ldm/lightricks/vocoders/vocoder.py``
implements (``resblock: "1"``, plain LeakyReLU resblocks) -- ``LTXVocoder``.
LTX23's embedded ``vocoder`` config is a DIFFERENT, nested shape
(``{"vocoder": {..., "resblock": "AMP1", "activation": "snakebeta"}, "bwe":
{...}}`` -- a two-stage vocoder + 16kHz->48kHz bandwidth-extension model) --
``LTXVocoderAMP``. The reference for the MAIN stage's building blocks
(``AMPBlock1``, ``SnakeBeta``, the anti-aliased ``Activation1d`` up/downsample
pair) lives at ``comfy/ldm/mmaudio/vae/bigvgan.py`` + ``activations.py`` +
``alias_free_torch.py`` (496 lines total, vendored here almost verbatim) --
built for the unrelated **mmaudio** family, but its ``AMPBlock1`` class and
``SnakeBeta``/``Activation1d`` primitives are architecturally identical to
what LTX23's real key shapes require (verified: `vocoder.vocoder.resblocks.*`
keys match ``AMPBlock1``'s ``convs1``/``convs2``/anti-aliased-activation
layout exactly, dilations ``[1,3,5]`` = ``AMPBlock1``'s default, not
``AMPBlock2``'s ``[1,3]``). Real key layout differs from ComfyUI's own
``BigVGANVocoder`` in one place: ``ups`` is a FLAT ``ConvTranspose1d`` list
(``ups.{i}.weight``), not ``BigVGANVocoder``'s nested
``ModuleList([ConvTranspose1d])`` (``ups.{i}.0.weight``) -- LTX23's AMP
vocoder is the *original LTX* ``Vocoder`` skeleton (``conv_pre``/flat
``ups``/``resblocks``/``conv_post``) with HiFi-GAN's plain-LeakyReLU
resblocks/final-activation swapped for BigVGAN's anti-aliased
SnakeBeta ones, not a wholesale ``BigVGANVocoder`` reuse.

**BWE bandwidth-extension (16kHz -> 48kHz)**: the ``bwe_generator``
submodule (same AMP-block architecture, smaller, its own 5-stage ``ups``) and the
``mel_stft`` buffers (``mel_basis``, STFT-as-conv1d ``forward_basis``/
``inverse_basis`` -- a standard NVIDIA-Tacotron-style STFT-via-conv1d matrix
pair) are composed by ``LTXVocoderAMP.forward`` following diffusers'
``LTX2VocoderWithBWE.forward`` (Apache-2.0,
``diffusers/pipelines/ltx2/vocoder.py``): the main stage's own 16kHz waveform is
re-analyzed by ``mel_stft`` into a fresh log-mel that conditions the BWE stage,
whose 48kHz residual is added to a Hann-window sinc-resampled skip of the same
waveform and clamped once to ``[-1, 1]``. The skip ``resampler``'s filter is
recomputed at load (non-persistent) -- no ``resampler.*`` keys exist in the
checkpoint (verified via header dump), so the key-parity gate expects none.
``SnakeBeta`` is log-space (hardcoded, see ``_SnakeBeta``) -- two independent
references confirm it, and log-space is required for the main-stage decode to
match the reference output.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule
from ..errors import NativeEngineUnsupportedError
from .ltx_causal_video import PixelNorm

LATENT_DOWNSAMPLE_FACTOR = 4
_LRELU_SLOPE = 0.1


class CausalityAxis:
    """String constants mirroring ComfyUI's ``CausalityAxis`` enum values."""

    NONE = "none"
    WIDTH = "width"
    HEIGHT = "height"
    WIDTH_COMPATIBILITY = "width-compatibility"

    @classmethod
    def normalize(cls, value: Any) -> str:
        if value is None:
            return cls.NONE
        v = str(value).lower()
        if v not in (cls.NONE, cls.WIDTH, cls.HEIGHT, cls.WIDTH_COMPATIBILITY):
            raise NativeEngineUnsupportedError(f"LTX audio VAE: unknown causality_axis {value!r}")
        return v


def _make_norm2d(num_channels: int, norm_type: str, operations: Any) -> nn.Module:
    if norm_type == "group":
        return operations.GroupNorm(num_groups=32, num_channels=num_channels, eps=1e-6, affine=True)
    if norm_type == "pixel":
        return PixelNorm(dim=1, eps=1e-6)
    raise NativeEngineUnsupportedError(f"LTX audio VAE: unsupported norm_type {norm_type!r}")


class _CausalConv2d(nn.Module):
    """Asymmetric (left/top-only, per axis) padded 2D conv -- ComfyUI's ``CausalConv2d``."""

    def __init__(self, in_channels, out_channels, kernel_size, *, stride=1, dilation=1,
                 groups=1, bias=True, causality_axis: str = CausalityAxis.HEIGHT, operations: Any) -> None:
        super().__init__()
        self.causality_axis = causality_axis
        kernel_size = nn.modules.utils._pair(kernel_size)
        dilation = nn.modules.utils._pair(dilation)
        pad_h = (kernel_size[0] - 1) * dilation[0]
        pad_w = (kernel_size[1] - 1) * dilation[1]
        if causality_axis == CausalityAxis.NONE:
            self.padding = (pad_w // 2, pad_w - pad_w // 2, pad_h // 2, pad_h - pad_h // 2)
        elif causality_axis in (CausalityAxis.WIDTH, CausalityAxis.WIDTH_COMPATIBILITY):
            self.padding = (pad_w, 0, pad_h // 2, pad_h - pad_h // 2)
        elif causality_axis == CausalityAxis.HEIGHT:
            self.padding = (pad_w // 2, pad_w - pad_w // 2, pad_h, 0)
        else:
            raise NativeEngineUnsupportedError(f"LTX audio VAE: invalid causality_axis {causality_axis!r}")

        self.conv = operations.Conv2d(
            in_channels, out_channels, kernel_size, stride=stride,
            padding=0, dilation=dilation, groups=groups, bias=bias,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(F.pad(x, self.padding))


def _make_conv2d(in_channels, out_channels, kernel_size, *, stride=1, causality_axis: str, operations: Any) -> nn.Module:
    if causality_axis != CausalityAxis.NONE:
        return _CausalConv2d(in_channels, out_channels, kernel_size, stride=stride,
                              causality_axis=causality_axis, operations=operations)
    padding = kernel_size // 2 if isinstance(kernel_size, int) else tuple(k // 2 for k in kernel_size)
    return operations.Conv2d(in_channels, out_channels, kernel_size, stride=stride, padding=padding)


class _AudioUpsample(nn.Module):
    def __init__(self, in_channels, with_conv: bool, *, causality_axis: str, operations: Any) -> None:
        super().__init__()
        self.with_conv = with_conv
        self.causality_axis = causality_axis
        if with_conv:
            self.conv = _make_conv2d(in_channels, in_channels, 3, stride=1,
                                      causality_axis=causality_axis, operations=operations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.interpolate(x, scale_factor=2.0, mode="nearest")
        if self.with_conv:
            x = self.conv(x)
            # Drop the causally-duplicated first element (see ComfyUI's Upsample
            # docstring for the interpolate-then-crop derivation this mirrors).
            if self.causality_axis == CausalityAxis.HEIGHT:
                x = x[:, :, 1:, :]
            elif self.causality_axis == CausalityAxis.WIDTH:
                x = x[:, :, :, 1:]
        return x


class _AudioResnetBlock(nn.Module):
    def __init__(self, *, in_channels, out_channels=None, dropout, norm_type: str,
                 causality_axis: str, operations: Any) -> None:
        super().__init__()
        if causality_axis != CausalityAxis.NONE and norm_type == "group":
            raise NativeEngineUnsupportedError("LTX audio VAE: causal ResnetBlock with GroupNorm is not supported")
        self.in_channels = in_channels
        self.out_channels = in_channels if out_channels is None else out_channels

        self.norm1 = _make_norm2d(in_channels, norm_type, operations)
        self.non_linearity = nn.SiLU()
        self.conv1 = _make_conv2d(in_channels, self.out_channels, 3, stride=1,
                                   causality_axis=causality_axis, operations=operations)
        self.norm2 = _make_norm2d(self.out_channels, norm_type, operations)
        self.dropout = nn.Dropout(dropout)
        self.conv2 = _make_conv2d(self.out_channels, self.out_channels, 3, stride=1,
                                   causality_axis=causality_axis, operations=operations)
        if self.in_channels != self.out_channels:
            self.nin_shortcut = _make_conv2d(in_channels, self.out_channels, 1, stride=1,
                                              causality_axis=causality_axis, operations=operations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.conv1(self.non_linearity(self.norm1(x)))
        h = self.conv2(self.dropout(self.non_linearity(self.norm2(h))))
        if self.in_channels != self.out_channels:
            x = self.nin_shortcut(x)
        return x + h


class _AudioDecoder(nn.Module):
    """Ported from ComfyUI's ``causal_audio_autoencoder.Decoder``."""

    def __init__(self, *, ch, out_ch, ch_mult, num_res_blocks, dropout=0.0,
                 resamp_with_conv=True, z_channels, norm_type="group",
                 causality_axis: str = CausalityAxis.WIDTH, operations: Any, **_ignore) -> None:
        super().__init__()
        self.out_ch = out_ch
        self.z_channels = z_channels
        num_resolutions = len(ch_mult)

        block_in = ch * ch_mult[num_resolutions - 1]
        self.conv_in = _make_conv2d(z_channels, block_in, 3, stride=1,
                                     causality_axis=causality_axis, operations=operations)
        self.non_linearity = nn.SiLU()

        self.mid = nn.Module()
        self.mid.block_1 = _AudioResnetBlock(in_channels=block_in, out_channels=block_in, dropout=dropout,
                                              norm_type=norm_type, causality_axis=causality_axis, operations=operations)
        self.mid.attn_1 = nn.Identity()
        self.mid.block_2 = _AudioResnetBlock(in_channels=block_in, out_channels=block_in, dropout=dropout,
                                              norm_type=norm_type, causality_axis=causality_axis, operations=operations)

        self.up = nn.ModuleList()
        for i_level in reversed(range(num_resolutions)):
            block = nn.ModuleList()
            block_out = ch * ch_mult[i_level]
            for _ in range(num_res_blocks + 1):
                block.append(_AudioResnetBlock(in_channels=block_in, out_channels=block_out, dropout=dropout,
                                                norm_type=norm_type, causality_axis=causality_axis, operations=operations))
                block_in = block_out
            up = nn.Module()
            up.block = block
            up.attn = nn.ModuleList()
            if i_level != 0:
                up.upsample = _AudioUpsample(block_in, resamp_with_conv,
                                              causality_axis=causality_axis, operations=operations)
            self.up.insert(0, up)

        self.norm_out = _make_norm2d(block_in, norm_type, operations)
        self.conv_out = _make_conv2d(block_in, out_ch, 3, stride=1,
                                      causality_axis=causality_axis, operations=operations)
        self._num_resolutions = num_resolutions

    @staticmethod
    def _adjust_output_shape(x: torch.Tensor, target_shape: tuple[int, int, int, int]) -> torch.Tensor:
        _, _, cur_t, cur_f = x.shape
        _, tgt_c, tgt_t, tgt_f = target_shape
        x = x[:, :tgt_c, : min(cur_t, tgt_t), : min(cur_f, tgt_f)]
        pad_t = tgt_t - x.shape[2]
        pad_f = tgt_f - x.shape[3]
        if pad_t > 0 or pad_f > 0:
            x = F.pad(x, (0, max(pad_f, 0), 0, max(pad_t, 0)))
        return x[:, :tgt_c, :tgt_t, :tgt_f]

    def forward(self, latents: torch.Tensor, *, target_shape: tuple[int, int, int, int]) -> torch.Tensor:
        h = self.conv_in(latents)
        h = self.mid.block_2(self.mid.attn_1(self.mid.block_1(h)))
        for level in reversed(range(self._num_resolutions)):
            up = self.up[level]
            for block in up.block:
                h = block(h)
            if level != 0:
                h = up.upsample(h)
        h = self.conv_out(self.non_linearity(self.norm_out(h)))
        return self._adjust_output_shape(h, target_shape)


class _AudioPerChannelStatistics(nn.Module):
    """Per-patch-channel mean/std, applied in patch space (matches ComfyUI's ``processor``)."""

    def __init__(self, channels: int) -> None:
        super().__init__()
        self.register_buffer("std-of-means", torch.empty(channels))
        self.register_buffer("mean-of-means", torch.empty(channels))

    def un_normalize(self, x: torch.Tensor) -> torch.Tensor:
        return x * self.get_buffer("std-of-means").to(x) + self.get_buffer("mean-of-means").to(x)

    def normalize(self, x: torch.Tensor) -> torch.Tensor:
        return (x - self.get_buffer("mean-of-means").to(x)) / self.get_buffer("std-of-means").to(x)


def _patchify_audio(latents: torch.Tensor) -> torch.Tensor:
    """``b c t f -> b t (c f)`` (c-major, f-minor) -- ``AudioPatchifier.patchify`` sans timings."""
    b, c, t, f = latents.shape
    return latents.permute(0, 2, 1, 3).reshape(b, t, c * f)


def _unpatchify_audio(x: torch.Tensor, *, channels: int, freq: int) -> torch.Tensor:
    """Inverse of :func:`_patchify_audio`."""
    b, t, _ = x.shape
    return x.reshape(b, t, channels, freq).permute(0, 2, 1, 3)


class LTXAudioAutoencoder(NativeArchModule):
    """LTX-2/2.3 causal-2D-conv audio VAE, decode-only.

    Key prefix in real checkpoints: ``audio_vae.*``. Both ``encoder`` and
    ``decoder`` submodules are constructed for exact key-set parity with the
    real checkpoint (verified: ``LTX2_audio_vae_bf16.safetensors`` ships both,
    102 tensors total), but only ``decode()`` is implemented -- encode is out
    of scope.
    """

    def __init__(self, *, ddconfig: dict, sampling_rate: int, mel_hop_length: int,
                 n_fft: int, operations: Any) -> None:
        super().__init__()
        self.sampling_rate = sampling_rate
        self.mel_bins = ddconfig.get("mel_bins", 64)
        self.mel_hop_length = mel_hop_length
        self.n_fft = n_fft
        self.causality_axis = CausalityAxis.normalize(ddconfig.get("causality_axis", CausalityAxis.WIDTH))

        # Real checkpoints ship both encoder.* and decoder.* (verified via header
        # dump) -- Encoder is a bare weight container here (no forward ported;
        # decode-only per approved scope), Decoder is the real, tested path.
        encoder_kwargs = dict(ddconfig)
        self.encoder = _make_encoder_weight_container(operations=operations, **encoder_kwargs)
        self.decoder = _AudioDecoder(operations=operations, **encoder_kwargs)

        freq_after_downsample = max(self.mel_bins // LATENT_DOWNSAMPLE_FACTOR, 1)
        stats_channels = ddconfig["z_channels"] * freq_after_downsample
        self.per_channel_statistics = _AudioPerChannelStatistics(stats_channels)

    @classmethod
    def from_config(cls, config: dict, operations: Any) -> "LTXAudioAutoencoder":
        model_params = config.get("model", {}).get("params", {})
        ddconfig = model_params.get("encoder", model_params.get("ddconfig"))
        if not isinstance(ddconfig, dict):
            raise NativeEngineUnsupportedError("LTX audio VAE: config has no model.params.ddconfig")
        sampling_rate = model_params.get("sampling_rate", config.get("sampling_rate", 16000))
        stft = config.get("preprocessing", {}).get("stft", {})
        return cls(
            ddconfig=ddconfig,
            sampling_rate=sampling_rate,
            mel_hop_length=stft.get("hop_length", 160),
            n_fft=stft.get("filter_length", 1024),
            operations=operations,
        )

    def post_load(self) -> None:
        return None

    def _target_shape_from_latents(self, latents_shape: torch.Size) -> tuple[int, int, int, int]:
        batch, _, time, _ = latents_shape
        target_length = time * LATENT_DOWNSAMPLE_FACTOR
        if self.causality_axis != CausalityAxis.NONE:
            target_length -= LATENT_DOWNSAMPLE_FACTOR - 1
        return (batch, self.decoder.out_ch, target_length, self.mel_bins)

    def decode(self, latents: torch.Tensor) -> torch.Tensor:
        """Normalized latents ``(batch, z_channels, time, freq_latent)`` -> mel-spectrogram."""
        b, c, t, f = latents.shape
        patched = _patchify_audio(latents)
        denorm = self.per_channel_statistics.un_normalize(patched)
        denorm = _unpatchify_audio(denorm, channels=c, freq=f)
        target_shape = self._target_shape_from_latents(latents.shape)
        return self.decoder(denorm, target_shape=target_shape)


def _make_encoder_weight_container(*, ch, out_ch, ch_mult, num_res_blocks, in_channels,
                                    z_channels, double_z=True, norm_type="group",
                                    causality_axis: str = CausalityAxis.WIDTH, operations: Any, **_ignore) -> nn.Module:
    """Weight-shape-faithful (but forward-less) mirror of ComfyUI's ``Encoder``.

    Exists purely so ``load_into_module`` gets exact key-set parity against
    real checkpoints, which ship ``encoder.*`` weights alongside ``decoder.*``
    even though encode is out of scope for this task (see module docstring).
    Calling ``forward`` raises -- there is deliberately no encode path here.
    """
    container = nn.Module()
    num_resolutions = len(ch_mult)
    container.conv_in = _make_conv2d(in_channels, ch, 3, stride=1, causality_axis=causality_axis, operations=operations)
    container.non_linearity = nn.SiLU()
    container.down = nn.ModuleList()
    in_ch_mult = (1,) + tuple(ch_mult)
    block_in = ch
    for i_level in range(num_resolutions):
        block = nn.ModuleList()
        block_in = ch * in_ch_mult[i_level]
        block_out = ch * ch_mult[i_level]
        for _ in range(num_res_blocks):
            block.append(_AudioResnetBlock(in_channels=block_in, out_channels=block_out, dropout=0.0,
                                            norm_type=norm_type, causality_axis=causality_axis, operations=operations))
            block_in = block_out
        down = nn.Module()
        down.block = block
        down.attn = nn.ModuleList()
        if i_level != num_resolutions - 1:
            down.downsample = _AudioDownsample(block_in, True, causality_axis=causality_axis, operations=operations)
        container.down.append(down)
    container.mid = nn.Module()
    container.mid.block_1 = _AudioResnetBlock(in_channels=block_in, out_channels=block_in, dropout=0.0,
                                               norm_type=norm_type, causality_axis=causality_axis, operations=operations)
    container.mid.attn_1 = nn.Identity()
    container.mid.block_2 = _AudioResnetBlock(in_channels=block_in, out_channels=block_in, dropout=0.0,
                                               norm_type=norm_type, causality_axis=causality_axis, operations=operations)
    container.norm_out = _make_norm2d(block_in, norm_type, operations)
    container.conv_out = _make_conv2d(block_in, 2 * z_channels if double_z else z_channels, 3, stride=1,
                                       causality_axis=causality_axis, operations=operations)

    def _no_encode(*_args, **_kwargs):
        raise NativeEngineUnsupportedError(
            "LTX audio VAE: encode is out of scope for this task (decode-only, approved) "
            "-- the encoder submodule exists for checkpoint key-parity only."
        )

    container.forward = _no_encode
    return container


class _AudioDownsample(nn.Module):
    """Ported from ComfyUI's ``causal_audio_autoencoder.Downsample`` (encoder-only, weights-only here)."""

    def __init__(self, in_channels, with_conv: bool, *, causality_axis: str, operations: Any) -> None:
        super().__init__()
        self.with_conv = with_conv
        self.causality_axis = causality_axis
        if with_conv:
            self.conv = operations.Conv2d(in_channels, in_channels, kernel_size=3, stride=2, padding=0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        raise NativeEngineUnsupportedError("LTX audio VAE: encode is out of scope for this task (decode-only, approved)")


# ---------------------------------------------------------------------------
# Vocoder (HiFi-GAN-v1-style, mel -> waveform)
# ---------------------------------------------------------------------------

def _get_padding(kernel_size: int, dilation: int = 1) -> int:
    return int((kernel_size * dilation - dilation) / 2)


class _ResBlock1(nn.Module):
    def __init__(self, channels, kernel_size, dilation, *, operations: Any) -> None:
        super().__init__()
        self.convs1 = nn.ModuleList([
            operations.Conv1d(channels, channels, kernel_size, 1, dilation=d, padding=_get_padding(kernel_size, d))
            for d in dilation
        ])
        self.convs2 = nn.ModuleList([
            operations.Conv1d(channels, channels, kernel_size, 1, dilation=1, padding=_get_padding(kernel_size, 1))
            for _ in dilation
        ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for c1, c2 in zip(self.convs1, self.convs2):
            xt = c1(F.leaky_relu(x, _LRELU_SLOPE))
            xt = c2(F.leaky_relu(xt, _LRELU_SLOPE))
            x = xt + x
        return x


class _ResBlock2(nn.Module):
    def __init__(self, channels, kernel_size, dilation, *, operations: Any) -> None:
        super().__init__()
        self.convs = nn.ModuleList([
            operations.Conv1d(channels, channels, kernel_size, 1, dilation=d, padding=_get_padding(kernel_size, d))
            for d in dilation
        ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for c in self.convs:
            xt = c(F.leaky_relu(x, _LRELU_SLOPE))
            x = xt + x
        return x


class LTXVocoder(NativeArchModule):
    """HiFi-GAN-v1-style vocoder, ported from ComfyUI's ``vocoders/vocoder.py``.

    Only the flat ``resblock: "1"``/``"2"`` HiFi-GAN shape (LTX2's real config)
    is supported; LTX23 uses ``LTXVocoderAMP`` instead -- see that class.
    """

    def __init__(self, *, config: dict, operations: Any) -> None:
        super().__init__()
        resblock_kernel_sizes = config.get("resblock_kernel_sizes", [3, 7, 11])
        upsample_rates = config.get("upsample_rates", [6, 5, 2, 2, 2])
        upsample_kernel_sizes = config.get("upsample_kernel_sizes", [16, 15, 8, 4, 4])
        resblock_dilation_sizes = config.get("resblock_dilation_sizes", [[1, 3, 5], [1, 3, 5], [1, 3, 5]])
        upsample_initial_channel = config.get("upsample_initial_channel", 1024)
        stereo = config.get("stereo", True)
        resblock = str(config.get("resblock", "1"))
        if resblock not in ("1", "2"):
            raise NativeEngineUnsupportedError(
                f"LTX vocoder: unsupported resblock type {resblock!r} (only HiFi-GAN '1'/'2' "
                "are vendored; 'AMP1'/snakebeta configs -- e.g. LTX23's -- are not)"
            )

        self.num_kernels = len(resblock_kernel_sizes)
        self.num_upsamples = len(upsample_rates)
        in_channels = 128 if stereo else 64
        self.conv_pre = operations.Conv1d(in_channels, upsample_initial_channel, 7, 1, padding=3)
        resblock_cls = _ResBlock1 if resblock == "1" else _ResBlock2

        self.ups = nn.ModuleList()
        for i, (u, k) in enumerate(zip(upsample_rates, upsample_kernel_sizes)):
            self.ups.append(operations.ConvTranspose1d(
                upsample_initial_channel // (2 ** i), upsample_initial_channel // (2 ** (i + 1)),
                k, u, padding=(k - u) // 2,
            ))

        self.resblocks = nn.ModuleList()
        ch = upsample_initial_channel
        for i in range(len(self.ups)):
            ch = upsample_initial_channel // (2 ** (i + 1))
            for k, d in zip(resblock_kernel_sizes, resblock_dilation_sizes):
                self.resblocks.append(resblock_cls(ch, k, d, operations=operations))

        out_channels = 2 if stereo else 1
        self.conv_post = operations.Conv1d(ch, out_channels, 7, 1, padding=3)
        self.upsample_factor = int(np.prod([self.ups[i].stride[0] for i in range(len(self.ups))]))
        self.output_sample_rate = config.get("output_sample_rate")

    @classmethod
    def from_config(cls, config: dict, operations: Any) -> "LTXVocoder":
        if "upsample_rates" not in config:
            raise NativeEngineUnsupportedError(
                "LTX vocoder: config is not the flat HiFi-GAN shape (no 'upsample_rates' key) "
                "-- likely LTX23's nested {'vocoder': ..., 'bwe': ...} config; use LTXVocoderAMP instead"
            )
        return cls(config=config, operations=operations)

    def post_load(self) -> None:
        return None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 4:
            assert x.shape[1] == 2, "Input must have 2 channels for stereo"
            x = torch.cat((x[:, 0, :, :], x[:, 1, :, :]), dim=1)
        x = self.conv_pre(x)
        for i in range(self.num_upsamples):
            x = self.ups[i](F.leaky_relu(x, _LRELU_SLOPE))
            xs = None
            for j in range(self.num_kernels):
                block_out = self.resblocks[i * self.num_kernels + j](x)
                xs = block_out if xs is None else xs + block_out
            x = xs / self.num_kernels
        x = self.conv_post(F.leaky_relu(x))
        return torch.tanh(x)


# ---------------------------------------------------------------------------
# LTX23's AMP1/SnakeBeta vocoder -- anti-aliased BigVGAN-style
# resblocks, vendored from comfy/ldm/mmaudio/vae/{bigvgan,activations,
# alias_free_torch}.py. See module docstring for the "flat ups, not
# BigVGANVocoder's nested ups" key-layout note and the bwe/mel_stft scope cut.
# ---------------------------------------------------------------------------

def _kaiser_sinc_filter1d(cutoff: float, half_width: float, kernel_size: int) -> torch.Tensor:
    even = kernel_size % 2 == 0
    half_size = kernel_size // 2
    delta_f = 4 * half_width
    A = 2.285 * (half_size - 1) * torch.pi * delta_f + 7.95
    if A > 50.0:
        beta = 0.1102 * (A - 8.7)
    elif A >= 21.0:
        beta = 0.5842 * (A - 21) ** 0.4 + 0.07886 * (A - 21.0)
    else:
        beta = 0.0
    window = torch.kaiser_window(kernel_size, beta=beta, periodic=False)
    time = (torch.arange(-half_size, half_size) + 0.5) if even else (torch.arange(kernel_size) - half_size)
    if cutoff == 0:
        return torch.zeros_like(time).view(1, 1, kernel_size)
    filt = 2 * cutoff * window * torch.sinc(2 * cutoff * time)
    filt = filt / filt.sum()
    return filt.view(1, 1, kernel_size)


class _LowPassFilter1d(nn.Module):
    def __init__(self, cutoff=0.5, half_width=0.6, stride=1, kernel_size=12) -> None:
        super().__init__()
        self.kernel_size = kernel_size
        self.pad_left = kernel_size // 2 - int(kernel_size % 2 == 0)
        self.pad_right = kernel_size // 2
        self.stride = stride
        self.register_buffer("filter", _kaiser_sinc_filter1d(cutoff, half_width, kernel_size))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        c = x.shape[1]
        x = F.pad(x, (self.pad_left, self.pad_right), mode="replicate")
        return F.conv1d(x, self.filter.expand(c, -1, -1).to(x), stride=self.stride, groups=c)


class _UpSample1d(nn.Module):
    """Sinc-interpolation upsampler.

    ``window_type="kaiser"`` (default) is BigVGAN's anti-aliasing filter, used
    inside ``_Activation1d`` -- its ``filter`` buffer IS stored in the real
    checkpoint (verified: ``...upsample.filter`` keys present), so it stays
    persistent. ``window_type="hann"`` is the BWE skip-connection resampler
    (``LTXVocoderAMP``): its filter is NOT in the checkpoint (``persistent=False``
    in the diffusers/Wan2GP references and no ``resampler.*`` keys exist in the
    header), so it must be recomputed at load and kept out of the state dict.
    """

    def __init__(self, ratio=2, kernel_size=None, *, window_type: str = "kaiser", persistent: bool = True) -> None:
        super().__init__()
        self.ratio = ratio
        self.stride = ratio
        if window_type == "hann":
            rolloff = 0.99
            lowpass_filter_width = 6
            width = math.ceil(lowpass_filter_width / rolloff)
            self.kernel_size = 2 * width * ratio + 1
            self.pad = width
            self.pad_left = 2 * width * ratio
            self.pad_right = self.kernel_size - ratio
            time_axis = (torch.arange(self.kernel_size) / ratio - width) * rolloff
            time_clamped = time_axis.clamp(-lowpass_filter_width, lowpass_filter_width)
            window = torch.cos(time_clamped * torch.pi / lowpass_filter_width / 2) ** 2
            filt = (torch.sinc(time_axis) * window * rolloff / ratio).view(1, 1, -1)
        else:
            self.kernel_size = int(6 * ratio // 2) * 2 if kernel_size is None else kernel_size
            self.pad = self.kernel_size // ratio - 1
            self.pad_left = self.pad * self.stride + (self.kernel_size - self.stride) // 2
            self.pad_right = self.pad * self.stride + (self.kernel_size - self.stride + 1) // 2
            filt = _kaiser_sinc_filter1d(0.5 / ratio, 0.6 / ratio, self.kernel_size)
        self.register_buffer("filter", filt, persistent=persistent)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        c = x.shape[1]
        x = F.pad(x, (self.pad, self.pad), mode="replicate")
        x = self.ratio * F.conv_transpose1d(x, self.filter.expand(c, -1, -1).to(x), stride=self.stride, groups=c)
        return x[..., self.pad_left:-self.pad_right]


class _DownSample1d(nn.Module):
    def __init__(self, ratio=2, kernel_size=None) -> None:
        super().__init__()
        kernel_size = int(6 * ratio // 2) * 2 if kernel_size is None else kernel_size
        self.lowpass = _LowPassFilter1d(cutoff=0.5 / ratio, half_width=0.6 / ratio, stride=ratio, kernel_size=kernel_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.lowpass(x)


class _SnakeBeta(nn.Module):
    """``x + 1/exp(beta) * sin^2(x * exp(alpha))`` -- per-channel trainable alpha/beta.

    **Log-space is hardcoded, not a config key.** The stored ``alpha``/``beta``
    tensors are in log-space and ``exp()``'d in forward. This is settled by two
    independent references, neither of which reads it from the checkpoint's
    ``vocoder``/``bwe`` config blocks (it is not a key there): diffusers'
    ``SnakeBeta`` (``logscale=True`` default) and Wan2GP's ``SnakeBeta``
    (``alpha_logscale=True`` default). It is therefore an architectural default
    of the BigVGAN-style anti-aliased vocoder, not a per-checkpoint
    hyperparameter -- the real LTX23 checkpoint's ``alpha``/``beta`` values are
    log-space, so a prior linear-space assumption mis-decoded even the shipped
    main stage. Params init to zeros so an un-loaded module is neutral
    (``exp(0) == 1``), matching diffusers.
    """

    def __init__(self, channels: int) -> None:
        super().__init__()
        self.alpha = nn.Parameter(torch.zeros(channels))
        self.beta = nn.Parameter(torch.zeros(channels))
        self._eps = 1e-9

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        alpha = torch.exp(self.alpha.to(x)).unsqueeze(0).unsqueeze(-1)
        beta = torch.exp(self.beta.to(x)).unsqueeze(0).unsqueeze(-1)
        return x + (1.0 / (beta + self._eps)) * torch.sin(x * alpha) ** 2


class _Activation1d(nn.Module):
    """Anti-aliased activation: upsample 2x -> activate -> downsample 2x."""

    def __init__(self, activation: nn.Module, up_ratio: int = 2, down_ratio: int = 2,
                 up_kernel_size: int = 12, down_kernel_size: int = 12) -> None:
        super().__init__()
        self.act = activation
        self.upsample = _UpSample1d(up_ratio, up_kernel_size)
        self.downsample = _DownSample1d(down_ratio, down_kernel_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.downsample(self.act(self.upsample(x)))


class _AMPBlock1(nn.Module):
    """BigVGAN's anti-aliased multi-periodicity resblock (3-dilation variant --
    matches LTX23's real ``resblock_dilation_sizes: [[1,3,5],[1,3,5],[1,3,5]]``,
    i.e. this is what ``"resblock": "AMP1"`` denotes, not ``AMPBlock2``'s
    2-dilation variant), with LTX-native ``acts1``/``acts2`` naming (matching
    ``_ResBlock1``'s ``convs1``/``convs2`` convention in this file) rather than
    ``BigVGANVocoder``'s flat ``self.activations`` ModuleList -- verified
    against the real checkpoint's key layout
    (``resblocks.N.acts1.{0,1,2}``/``acts2.{0,1,2}``, not
    ``resblocks.N.activations.{0..5}``)."""

    def __init__(self, channels, kernel_size, dilation, *, operations: Any) -> None:
        super().__init__()
        self.convs1 = nn.ModuleList([
            operations.Conv1d(channels, channels, kernel_size, 1, dilation=d, padding=_get_padding(kernel_size, d))
            for d in dilation
        ])
        self.convs2 = nn.ModuleList([
            operations.Conv1d(channels, channels, kernel_size, 1, dilation=1, padding=_get_padding(kernel_size, 1))
            for _ in dilation
        ])
        self.acts1 = nn.ModuleList([_Activation1d(_SnakeBeta(channels)) for _ in dilation])
        self.acts2 = nn.ModuleList([_Activation1d(_SnakeBeta(channels)) for _ in dilation])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for c1, c2, a1, a2 in zip(self.convs1, self.convs2, self.acts1, self.acts2):
            xt = c2(a2(c1(a1(x))))
            x = xt + x
        return x


class _AMPVocoderStage(nn.Module):
    """One AMP1/SnakeBeta vocoder stage -- the *original LTX* ``Vocoder``
    skeleton (flat ``conv_pre``/``ups``/``resblocks``/``conv_post``, not
    ``BigVGANVocoder``'s nested ``ups``) with BigVGAN's anti-aliased resblocks
    and final activation swapping in for HiFi-GAN's plain LeakyReLU ones.
    Used for both ``vocoder.vocoder.*`` (main, 16kHz-native) and
    ``vocoder.bwe_generator.*`` (48kHz bandwidth-extension, key-parity only --
    see module docstring) with different configs.
    """

    def __init__(self, *, in_channels: int, resblock_kernel_sizes, upsample_rates,
                 upsample_kernel_sizes, resblock_dilation_sizes, upsample_initial_channel: int,
                 out_channels: int, use_bias_at_final: bool = True, use_tanh_at_final: bool = True,
                 operations: Any) -> None:
        super().__init__()
        self.num_kernels = len(resblock_kernel_sizes)
        self.num_upsamples = len(upsample_rates)
        self.use_tanh_at_final = use_tanh_at_final

        self.conv_pre = operations.Conv1d(in_channels, upsample_initial_channel, 7, 1, padding=3)
        self.ups = nn.ModuleList()
        for i, (u, k) in enumerate(zip(upsample_rates, upsample_kernel_sizes)):
            self.ups.append(operations.ConvTranspose1d(
                upsample_initial_channel // (2 ** i), upsample_initial_channel // (2 ** (i + 1)),
                k, u, padding=(k - u) // 2,
            ))

        self.resblocks = nn.ModuleList()
        ch = upsample_initial_channel
        for i in range(len(self.ups)):
            ch = upsample_initial_channel // (2 ** (i + 1))
            for k, d in zip(resblock_kernel_sizes, resblock_dilation_sizes):
                self.resblocks.append(_AMPBlock1(ch, k, d, operations=operations))

        self.act_post = _Activation1d(_SnakeBeta(ch))
        self.conv_post = operations.Conv1d(ch, out_channels, 7, 1, padding=3, bias=use_bias_at_final)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.conv_pre(x)
        for i in range(self.num_upsamples):
            x = self.ups[i](x)
            xs = None
            for j in range(self.num_kernels):
                block_out = self.resblocks[i * self.num_kernels + j](x)
                xs = block_out if xs is None else xs + block_out
            x = xs / self.num_kernels
        x = self.conv_post(self.act_post(x))
        return torch.tanh(x) if self.use_tanh_at_final else x


class _MelSTFTBuffers(nn.Module):
    """Causal log-mel STFT-as-conv1d (``mel_stft.*``), used by the BWE stage to
    re-analyze the main stage's own waveform. Buffer shapes come straight from
    the real checkpoint header (``mel_basis`` ``[64,257]``, ``forward_basis``/
    ``inverse_basis`` ``[514,1,512]``). Ported from diffusers'
    ``MelSTFT``/``CausalSTFT`` (NVIDIA-Tacotron-style DFT-basis conv):
    causal left-pad ``window_length - hop_length``, magnitude from the
    real/imag halves of the DFT output, ``log(clamp(mel_basis @ magnitude,
    1e-5))``. ``inverse_basis`` is loaded for key parity but stays inert (the
    forward is analysis-only; both references leave it unused here). Phase/energy
    are likewise not computed -- they are dead outputs of the reference's
    ``mel_spectrogram`` and unused by the BWE forward.
    """

    def __init__(self, *, n_mel: int, n_freq: int, stft_basis_channels: int,
                 filter_length: int, hop_length: int, window_length: int) -> None:
        super().__init__()
        self.hop_length = hop_length
        self.window_length = window_length
        self.register_buffer("mel_basis", torch.empty(n_mel, n_freq))
        self.stft_fn = nn.Module()
        self.stft_fn.register_buffer("forward_basis", torch.empty(stft_basis_channels, 1, filter_length))
        self.stft_fn.register_buffer("inverse_basis", torch.empty(stft_basis_channels, 1, filter_length))

    def forward(self, waveform: torch.Tensor) -> torch.Tensor:
        """Waveform ``(N, num_samples)`` or ``(N, 1, num_samples)`` -> log-mel
        ``(N, n_mel, num_frames)``."""
        if waveform.ndim == 2:
            waveform = waveform.unsqueeze(1)
        left_pad = max(0, self.window_length - self.hop_length)  # causal: left-only
        waveform = F.pad(waveform, (left_pad, 0))
        forward_basis = self.stft_fn.forward_basis.to(waveform)
        spec = F.conv1d(waveform, forward_basis, stride=self.hop_length, padding=0)
        n_freqs = spec.shape[1] // 2
        real, imag = spec[:, :n_freqs], spec[:, n_freqs:]
        magnitude = torch.sqrt(real ** 2 + imag ** 2)
        mel = torch.matmul(self.mel_basis.to(magnitude), magnitude)
        return torch.log(torch.clamp(mel, min=1e-5))


class LTXVocoderAMP(NativeArchModule):
    """LTX23's two-stage AMP1/SnakeBeta vocoder with bandwidth extension.

    ``forward``/``decode`` runs the full chain: the main stage (mel ->
    16kHz-native waveform) followed by the ``bwe_generator`` bandwidth-extension
    stage (16kHz -> 48kHz), composed exactly as diffusers'
    ``LTX2VocoderWithBWE.forward``. The ``mel_stft`` buffers re-analyze the main
    stage's own waveform to condition the BWE stage, and a Hann-window sinc
    ``resampler`` provides the residual skip connection (its filter is recomputed
    at load, not stored -- see ``_UpSample1d``). BWE is always-on for LTX23
    (every LTX23 checkpoint ships a ``bwe`` config block + weights); the reported
    output sample rate is ``output_sampling_rate`` (48000).
    """

    def __init__(self, *, main_config: dict, bwe_config: dict, operations: Any) -> None:
        super().__init__()

        def _stage(cfg: dict) -> _AMPVocoderStage:
            stereo = cfg.get("stereo", True)
            return _AMPVocoderStage(
                in_channels=128 if stereo else 64,
                out_channels=2 if stereo else 1,
                resblock_kernel_sizes=cfg.get("resblock_kernel_sizes", [3, 7, 11]),
                upsample_rates=cfg["upsample_rates"],
                upsample_kernel_sizes=cfg["upsample_kernel_sizes"],
                resblock_dilation_sizes=cfg.get("resblock_dilation_sizes", [[1, 3, 5], [1, 3, 5], [1, 3, 5]]),
                upsample_initial_channel=cfg.get("upsample_initial_channel", 1536),
                use_bias_at_final=cfg.get("use_bias_at_final", True),
                use_tanh_at_final=cfg.get("use_tanh_at_final", True),
                operations=operations,
            )

        self.vocoder = _stage(main_config)
        self.bwe_generator = _stage(bwe_config)

        # BWE re-analysis geometry (the bwe config block carries the STFT/mel and
        # sample-rate metadata -- see the checkpoint header dump in the research
        # report). n_freq = n_fft//2+1; the DFT-basis conv stacks real+imag, so
        # forward/inverse_basis have 2*n_freq output channels.
        n_fft = int(bwe_config.get("n_fft", 512))
        n_freq = n_fft // 2 + 1
        num_mels = int(bwe_config.get("num_mels", 64))
        self.hop_length = int(bwe_config.get("hop_length", 80))
        window_length = int(bwe_config.get("win_size", n_fft))
        self.input_sampling_rate = int(bwe_config.get("input_sampling_rate", 16000))
        self.output_sampling_rate = int(bwe_config.get("output_sampling_rate", 48000))

        self.mel_stft = _MelSTFTBuffers(
            n_mel=num_mels, n_freq=n_freq, stft_basis_channels=2 * n_freq,
            filter_length=n_fft, hop_length=self.hop_length, window_length=window_length,
        )
        # Sinc/Hann skip resampler -- ratio = 48000//16000 = 3. Filter is
        # recomputed here (non-persistent), not loaded: no resampler.* keys
        # exist in the checkpoint, so the key-parity gate must not expect any.
        self.resampler = _UpSample1d(
            ratio=self.output_sampling_rate // self.input_sampling_rate,
            window_type="hann", persistent=False,
        )
        self.output_sample_rate = self.output_sampling_rate

    @classmethod
    def from_config(cls, config: dict, operations: Any) -> "LTXVocoderAMP":
        main_config = config.get("vocoder")
        bwe_config = config.get("bwe")
        if not isinstance(main_config, dict) or "resblock" not in main_config:
            raise NativeEngineUnsupportedError(
                "LTX vocoder (AMP): expected a nested {'vocoder': {...}, 'bwe': {...}} config"
            )
        resblock = str(main_config.get("resblock", ""))
        if resblock.upper() != "AMP1":
            raise NativeEngineUnsupportedError(
                f"LTX vocoder (AMP): unsupported resblock type {resblock!r} (only 'AMP1' is vendored)"
            )
        if not isinstance(bwe_config, dict):
            raise NativeEngineUnsupportedError("LTX vocoder (AMP): missing 'bwe' config section")
        return cls(main_config=main_config, bwe_config=bwe_config, operations=operations)

    def post_load(self) -> None:
        return None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Mel-conditioned vocoder input -> 48kHz waveform (main + BWE stages).

        Composition mirrors diffusers' ``LTX2VocoderWithBWE.forward``: stage-1
        vocoder -> right-zero-pad to a ``hop_length`` multiple -> re-analyze the
        stage-1 waveform into a fresh log-mel -> BWE stage predicts a residual at
        48kHz -> add the sinc-resampled stage-1 waveform (skip) -> single final
        ``clamp(-1, 1)`` (both stages set ``use_tanh_at_final=false``) truncated
        to the exact output sample count.
        """
        if x.dim() == 4:
            assert x.shape[1] == 2, "Input must have 2 channels for stereo"
            x = torch.cat((x[:, 0, :, :], x[:, 1, :, :]), dim=1)

        x = self.vocoder(x)  # stage-1: (batch, channels, num_samples) at input rate
        _batch, num_channels, num_samples = x.shape

        # Right-zero-pad to an exact hop_length multiple so the re-analysis frame
        # count lines up; the skip connection uses this SAME padded waveform.
        remainder = num_samples % self.hop_length
        if remainder != 0:
            x = F.pad(x, (0, self.hop_length - remainder))

        # Re-analyze the stage-1 waveform itself (NOT the original input mel).
        log_mel = self.mel_stft(x.flatten(0, 1))  # (batch*channels, n_mel, frames)
        log_mel = log_mel.unflatten(0, (-1, num_channels))  # (batch, channels, n_mel, frames)
        mel_for_bwe = log_mel.flatten(1, 2)  # (batch, channels*n_mel = 128, frames)

        residual = self.bwe_generator(mel_for_bwe)
        skip = self.resampler(x)
        waveform = torch.clamp(residual + skip, -1, 1)

        output_samples = num_samples * self.output_sampling_rate // self.input_sampling_rate
        return waveform[..., :output_samples]


def decode_audio_waveform(autoencoder: LTXAudioAutoencoder, vocoder: LTXVocoder | LTXVocoderAMP,
                           latents: torch.Tensor) -> tuple[torch.Tensor, int]:
    """Normalized audio latents -> ``(waveform, sample_rate)``. The native-engine
    decode entry point composing the two modules above, mirroring ComfyUI's
    ``AudioVAE.decode`` + ``AudioVAE.run_vocoder``.
    """
    mel_spec = autoencoder.decode(latents)
    audio_channels = autoencoder.decoder.out_ch
    vocoder_input = mel_spec.transpose(2, 3)
    if audio_channels == 1:
        vocoder_input = vocoder_input.squeeze(1)
    elif audio_channels != 2:
        raise NativeEngineUnsupportedError(f"LTX audio VAE: unsupported audio_channels {audio_channels}")
    waveform = vocoder(vocoder_input)
    # The vocoder owns the true output rate: LTXVocoderAMP upsamples to 48kHz via
    # its BWE stage, while the flat LTXVocoder leaves it None (LTX2's config has no
    # output_sample_rate) and falls back to the audio VAE's own sampling rate.
    sample_rate = getattr(vocoder, "output_sample_rate", None) or autoencoder.sampling_rate
    return waveform, sample_rate
