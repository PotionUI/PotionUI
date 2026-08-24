"""LTX multi-scale ``LatentUpsampler``, vendored from ComfyUI's
``comfy/ldm/lightricks/latent_upsampler.py``.

All three modes are implemented -- spatial-only, temporal-only, and both --
selected from the checkpoint's own embedded config
(``detect_ltx_latent_upsampler_config``, loaded by ``load_ltx_latent_upsampler``
and driven by ``latent_upscaler/ltx``). The spatial path runs in production
against ``ltx-2.3-spatial-upscaler-*``; the temporal path is verified against
the references (see below) but has no local checkpoint yet, so it is covered
by synthetic configs only.

The temporal branch's **first-frame drop** after upsampling (``forward``
below) is not an implementation detail: it is what makes a ``T``-frame latent
come back as ``2T - 1`` rather than ``2T``, keeping the causal VAE's
``8k + 1`` frame lattice intact across a temporal round. Verified identical in
both references -- diffusers ``pipelines/ltx2/latent_upsampler.py:272-274``
and Lightricks ``ltx_core/model/upsampler/model.py:109-113``.

Config defaults mirror ComfyUI's ``LatentUpsampler.from_config`` and
Lightricks' own ``LatentUpsamplerConfigurator.from_metadata``, which agree
key-for-key (``in_channels=128`` -- the LTX video VAE's DiT-facing latent
width, not the mel-audio path -- ``mid_channels=512``, ``spatial_scale=2.0``,
``rational_resampler=False``). Diffusers renames two of them
(``rational_spatial_scale`` / ``use_rational_resampler``) and changes their
defaults, but that renaming is internal to diffusers: the shipped checkpoints
carry the Lightricks spelling, which is what ``from_config`` reads.

**Key-layout note**: ComfyUI's own module builds every conv/norm with bare
``torch.nn.Conv2d``/``Conv3d``/``GroupNorm`` -- it does NOT route through
``comfy.ops`` at all (no ``import comfy.ops`` in the source), unlike every
other lightricks VAE module. That's presumably fine for ComfyUI since this
model is small and typically run at full precision, but it doesn't match our
engine's "every parameterised layer goes through the ``operations`` seam"
convention, so this module builds through ``operations`` instead -- purely
additive (enables fp8/manual-cast dispatch later), keys and shapes are
identical either way.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule
from ..errors import NativeEngineUnsupportedError

_RATIONAL_SCALE_MAP = {0.75: (3, 4), 1.5: (3, 2), 2.0: (2, 1), 4.0: (4, 1)}


def _rational_for_scale(scale: float) -> tuple[int, int]:
    key = float(scale)
    if key not in _RATIONAL_SCALE_MAP:
        raise NativeEngineUnsupportedError(
            f"LTX latent upsampler: unsupported spatial_scale {scale} (choose from {sorted(_RATIONAL_SCALE_MAP)})"
        )
    return _RATIONAL_SCALE_MAP[key]


# `_BlurDownsample`'s fixed 5-tap kernel, `padding=2` -- see that class below.
# A stride-`den` conv2d with these two constants has output size
# `floor((in + 2*padding - kernel_size) / den) + 1 == floor((in - 1) / den) + 1`
# (`2*2 - 5 == -1`), independent of channel count/weights.
_BLUR_KERNEL_SIZE = 5
_BLUR_PADDING = 2


def rational_resample_out_size(in_size: int, scale: float) -> int:
    """Closed-form per-axis latent size `_SpatialRationalResampler.forward`
    produces for a latent axis of size ``in_size`` at ``scale``: an exact
    ``num``x pixel-shuffle followed by a stride-``den`` blur_down. Pure
    arithmetic (no tensors/weights touched) -- the single source of truth the
    preflight geometry check (``latent_upscaler/ltx/geometry.py``) reuses to
    predict the resampler's real output shape without loading a checkpoint or
    running the module, kept byte-for-byte identical to the module's own
    shape math via ``tests/platform/runtime/native/vae/test_ltx_latent_upsampler.py``.
    """
    num, den = _rational_for_scale(scale)
    in_after_shuffle = int(in_size) * num
    return (in_after_shuffle - _BLUR_KERNEL_SIZE + 2 * _BLUR_PADDING) // den + 1


class _PixelShuffleND(nn.Module):
    """No learned params -- a pure reshape, ported without ``einops``."""

    def __init__(self, dims: int, upscale_factors: tuple[int, ...]) -> None:
        super().__init__()
        if dims not in (1, 2, 3):
            raise NativeEngineUnsupportedError(f"LTX latent upsampler: PixelShuffleND dims must be 1/2/3, got {dims}")
        self.dims = dims
        self.upscale_factors = upscale_factors

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.dims == 3:
            p1, p2, p3 = self.upscale_factors
            b, cp, d, h, w = x.shape
            c = cp // (p1 * p2 * p3)
            x = x.view(b, c, p1, p2, p3, d, h, w)
            x = x.permute(0, 1, 5, 2, 6, 3, 7, 4)
            return x.reshape(b, c, d * p1, h * p2, w * p3)
        if self.dims == 2:
            p1, p2 = self.upscale_factors
            b, cp, h, w = x.shape
            c = cp // (p1 * p2)
            x = x.view(b, c, p1, p2, h, w)
            x = x.permute(0, 1, 4, 2, 5, 3)
            return x.reshape(b, c, h * p1, w * p2)
        # dims == 1
        (p1,) = self.upscale_factors
        b, cp, f, h, w = x.shape
        c = cp // p1
        x = x.view(b, c, p1, f, h, w)
        x = x.permute(0, 1, 3, 2, 4, 5)
        return x.reshape(b, c, f * p1, h, w)


class _BlurDownsample(nn.Module):
    """Fixed (non-learned) anti-aliased downsample -- a registered buffer, no ``operations`` needed."""

    def __init__(self, dims: int, stride: int) -> None:
        super().__init__()
        if dims not in (2, 3):
            raise NativeEngineUnsupportedError(f"LTX latent upsampler: BlurDownsample dims must be 2/3, got {dims}")
        self.dims = dims
        self.stride = stride
        k = torch.tensor([1.0, 4.0, 6.0, 4.0, 1.0])
        assert k.shape[0] == _BLUR_KERNEL_SIZE
        k2d = k[:, None] @ k[None, :]
        k2d = (k2d / k2d.sum()).float()
        self.register_buffer("kernel", k2d[None, None, :, :])

    def _apply_2d(self, x2d: torch.Tensor) -> torch.Tensor:
        if self.stride == 1:
            return x2d
        c = x2d.shape[1]
        weight = self.kernel.expand(c, 1, _BLUR_KERNEL_SIZE, _BLUR_KERNEL_SIZE).to(x2d)
        return F.conv2d(x2d, weight=weight, bias=None, stride=self.stride, padding=_BLUR_PADDING, groups=c)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.stride == 1:
            return x
        if self.dims == 2:
            return self._apply_2d(x)
        b, c, f, h, w = x.shape
        x = x.permute(0, 2, 1, 3, 4).reshape(b * f, c, h, w)
        x = self._apply_2d(x)
        h2, w2 = x.shape[-2:]
        return x.reshape(b, f, c, h2, w2).permute(0, 2, 1, 3, 4)


class _SpatialRationalResampler(nn.Module):
    def __init__(self, mid_channels: int, scale: float, *, operations: Any) -> None:
        super().__init__()
        self.scale = float(scale)
        self.num, self.den = _rational_for_scale(self.scale)
        self.conv = operations.Conv2d(mid_channels, (self.num ** 2) * mid_channels, kernel_size=3, padding=1)
        self.pixel_shuffle = _PixelShuffleND(2, (self.num, self.num))
        self.blur_down = _BlurDownsample(dims=2, stride=self.den)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, c, f, h, w = x.shape
        x = x.permute(0, 2, 1, 3, 4).reshape(b * f, c, h, w)
        x = self.blur_down(self.pixel_shuffle(self.conv(x)))
        _, c2, h2, w2 = x.shape
        return x.reshape(b, f, c2, h2, w2).permute(0, 2, 1, 3, 4)


class _UpsamplerResBlock(nn.Module):
    def __init__(self, channels: int, *, dims: int, operations: Any) -> None:
        super().__init__()
        conv_cls = operations.Conv2d if dims == 2 else operations.Conv3d
        self.conv1 = conv_cls(channels, channels, kernel_size=3, padding=1)
        self.norm1 = operations.GroupNorm(32, channels)
        self.conv2 = conv_cls(channels, channels, kernel_size=3, padding=1)
        self.norm2 = operations.GroupNorm(32, channels)
        self.activation = nn.SiLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x
        h = self.activation(self.norm1(self.conv1(x)))
        h = self.norm2(self.conv2(h))
        return self.activation(h + residual)


class LTXLatentUpsampler(NativeArchModule):
    """Spatially (and optionally temporally) upsamples LTX video-VAE latents
    in latent space, ahead of a second VAE-decode-and-re-encode pass.
    Construction/load-integrity only in this engine so far -- see module
    docstring for why no real checkpoint exists locally to test against.
    """

    def __init__(self, *, in_channels: int = 128, mid_channels: int = 512,
                 num_blocks_per_stage: int = 4, dims: int = 3,
                 spatial_upsample: bool = True, temporal_upsample: bool = False,
                 spatial_scale: float = 2.0, rational_resampler: bool = False,
                 operations: Any) -> None:
        super().__init__()
        if not spatial_upsample and not temporal_upsample:
            raise NativeEngineUnsupportedError(
                "LTX latent upsampler: either spatial_upsample or temporal_upsample must be True"
            )
        # Neither reference defines this combination: both build the temporal
        # upsampler out of Conv3d, and both route dims=2 through a 4D
        # per-frame forward that would hand that Conv3d a 4D tensor. Refuse at
        # construction instead of crashing mid-forward -- no config that works
        # today can reach this.
        if temporal_upsample and dims != 3:
            raise NativeEngineUnsupportedError(
                f"LTX latent upsampler: temporal_upsample requires dims=3, got dims={dims}"
            )
        self.in_channels = in_channels
        self.mid_channels = mid_channels
        self.num_blocks_per_stage = num_blocks_per_stage
        self.dims = dims
        self.spatial_upsample = spatial_upsample
        self.temporal_upsample = temporal_upsample
        self.spatial_scale = float(spatial_scale)
        self.rational_resampler = rational_resampler

        conv_cls = operations.Conv2d if dims == 2 else operations.Conv3d

        self.initial_conv = conv_cls(in_channels, mid_channels, kernel_size=3, padding=1)
        self.initial_norm = operations.GroupNorm(32, mid_channels)
        self.initial_activation = nn.SiLU()

        self.res_blocks = nn.ModuleList(
            [_UpsamplerResBlock(mid_channels, dims=dims, operations=operations) for _ in range(num_blocks_per_stage)]
        )

        if spatial_upsample and temporal_upsample:
            self.upsampler = nn.Sequential(
                operations.Conv3d(mid_channels, 8 * mid_channels, kernel_size=3, padding=1),
                _PixelShuffleND(3, (2, 2, 2)),
            )
        elif spatial_upsample:
            if rational_resampler:
                self.upsampler = _SpatialRationalResampler(mid_channels=mid_channels, scale=self.spatial_scale, operations=operations)
            else:
                self.upsampler = nn.Sequential(
                    operations.Conv2d(mid_channels, 4 * mid_channels, kernel_size=3, padding=1),
                    _PixelShuffleND(2, (2, 2)),
                )
        else:  # temporal_upsample only
            self.upsampler = nn.Sequential(
                operations.Conv3d(mid_channels, 2 * mid_channels, kernel_size=3, padding=1),
                _PixelShuffleND(1, (2,)),
            )

        self.post_upsample_res_blocks = nn.ModuleList(
            [_UpsamplerResBlock(mid_channels, dims=dims, operations=operations) for _ in range(num_blocks_per_stage)]
        )
        self.final_conv = conv_cls(mid_channels, in_channels, kernel_size=3, padding=1)

    @classmethod
    def from_config(cls, config: dict, operations: Any) -> "LTXLatentUpsampler":
        return cls(
            in_channels=config.get("in_channels", 128),
            mid_channels=config.get("mid_channels", 512),
            num_blocks_per_stage=config.get("num_blocks_per_stage", 4),
            dims=config.get("dims", 3),
            spatial_upsample=config.get("spatial_upsample", True),
            temporal_upsample=config.get("temporal_upsample", False),
            spatial_scale=config.get("spatial_scale", 2.0),
            rational_resampler=config.get("rational_resampler", False),
            operations=operations,
        )

    def post_load(self) -> None:
        return None

    def forward(self, latent: torch.Tensor) -> torch.Tensor:
        b, c, f, h, w = latent.shape

        if self.dims == 2:
            x = latent.permute(0, 2, 1, 3, 4).reshape(b * f, c, h, w)
            x = self.initial_activation(self.initial_norm(self.initial_conv(x)))
            for block in self.res_blocks:
                x = block(x)
            x = self.upsampler(x)
            for block in self.post_upsample_res_blocks:
                x = block(x)
            x = self.final_conv(x)
            _, c2, h2, w2 = x.shape
            return x.reshape(b, f, c2, h2, w2).permute(0, 2, 1, 3, 4)

        x = self.initial_activation(self.initial_norm(self.initial_conv(latent)))
        for block in self.res_blocks:
            x = block(x)

        if self.temporal_upsample:
            x = self.upsampler(x)
            x = x[:, :, 1:, :, :]
        elif isinstance(self.upsampler, _SpatialRationalResampler):
            x = self.upsampler(x)
        else:
            _, c_, f_, h_, w_ = x.shape
            x = x.permute(0, 2, 1, 3, 4).reshape(b * f_, c_, h_, w_)
            x = self.upsampler(x)
            _, c2, h2, w2 = x.shape
            x = x.reshape(b, f_, c2, h2, w2).permute(0, 2, 1, 3, 4)

        for block in self.post_upsample_res_blocks:
            x = block(x)
        return self.final_conv(x)
