"""Flux 2D image autoencoder (VAE), vendored from ComfyUI.

Covers both local checkpoint shapes (verified by header dump + real-file load
in ``tests/core/native/vae/test_ae_2d.py``):

  * ``ae.sft``   (Flux1, ``flux_ae``)  -- 16ch latent, ldm-style keys
    (``encoder.down.{i}.block.{j}.*`` / ``decoder.mid.attn_1.*``), no
    ``quant_conv``/``post_quant_conv``, no batchnorm. Regularizer applied
    directly to the encoder's raw output (``comfy.ldm.models.autoencoder
    .AutoencodingEngine`` shape).
  * ``flux2-vae.safetensors`` (Flux2/Klein, ``flux2_ae``) -- 32ch latent,
    **diffusers-style** keys (``encoder.down_blocks.{i}.resnets.{j}.*`` /
    ``decoder.mid_block.attentions.0.*``), plus ``quant_conv``/
    ``post_quant_conv`` and a top-level ``bn.*`` (BatchNorm2d, affine=False)
    that packs the regularized latent through a 2x2 pixel-unshuffle before
    normalizing (``comfy.ldm.models.autoencoder.AutoencodingEngineLegacy``
    shape, with ``batch_norm_latent`` on).

Both checkpoints share the *same* encoder/decoder hyperparameters (verified
against both header dumps): ``ch=128``, ``ch_mult=(1,2,4,4)``,
``num_res_blocks=2``, no attention in the down/up stacks (attention only in
the bottleneck), ``resolution=256`` (8x spatial downscale), ``double_z=True``.
Only ``z_channels`` (== the detected ``latent_channels``) and the
quant_conv/bn presence differ between the two variants.

**Key-layout handling**: rather than building two different module shapes for
the two key layouts, the loader (``load_vae`` in this package) renames the
diffusers-style flux2 keys to the same ldm-style layout this module is built
in, via the vendored ``convert_vae_state_dict`` (from ComfyUI's
``diffusers_convert.py`` -- verified: this is exactly the code path ComfyUI's
own ``VAE.__init__`` takes for this checkpoint, since it contains the
``decoder.up_blocks.0.resnets.0.norm1.weight`` signature key that triggers
diffusers conversion). This gives exact key-set parity against a single
module definition instead of maintaining two arch variants.

Latent scale/shift (``latent_formats.py``): Flux1 ``scale_factor=0.3611``,
``shift_factor=0.1159``; Flux2 uses the ``LatentFormat`` base-class defaults
``scale_factor=1.0``, ``shift_factor=0.0`` (ComfyUI's ``Flux2`` latent format
class does not override them). These are already correctly recorded in
``detect/registry.py`` (flux1/flux2 ``latent_format`` dicts) -- no
change needed there.
"""

from __future__ import annotations

import logging
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule

logger = logging.getLogger(__name__)

# Fixed architecture constants shared by both known checkpoints (verified by
# header dump: identical down/up block channel progression and block counts).
_CH = 128
_CH_MULT = (1, 2, 4, 4)
_NUM_RES_BLOCKS = 2
_ATTN_RESOLUTIONS: tuple[int, ...] = ()
_RESOLUTION = 256
_DROPOUT = 0.0

# Batchnorm-packing constants (flux2_ae only), matching ComfyUI's
# AutoencodingEngineLegacy(batch_norm_latent=True) exactly.
_BN_PACK_FACTOR = 2  # pixel-(un)shuffle factor
_BN_EPS = 1e-4
_BN_MOMENTUM = 0.1


def _normalize(operations: Any, channels: int) -> nn.Module:
    return operations.GroupNorm(num_groups=32, num_channels=channels, eps=1e-6, affine=True)


class ResnetBlock2D(nn.Module):
    """Pre-norm SiLU conv residual block (no timestep conditioning: temb_ch=0)."""

    def __init__(self, *, in_channels: int, out_channels: int, operations: Any) -> None:
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels

        self.norm1 = _normalize(operations, in_channels)
        self.conv1 = operations.Conv2d(in_channels, out_channels, kernel_size=3, stride=1, padding=1)
        self.norm2 = _normalize(operations, out_channels)
        self.conv2 = operations.Conv2d(out_channels, out_channels, kernel_size=3, stride=1, padding=1)

        if in_channels != out_channels:
            self.nin_shortcut = operations.Conv2d(in_channels, out_channels, kernel_size=1, stride=1, padding=0)
        else:
            self.nin_shortcut = None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.norm1(x)
        h = F.silu(h)
        h = self.conv1(h)
        h = self.norm2(h)
        h = F.silu(h)
        h = self.conv2(h)
        if self.nin_shortcut is not None:
            x = self.nin_shortcut(x)
        return x + h


class AttnBlock2D(nn.Module):
    """Single-head full self-attention over spatial positions (VAE bottleneck)."""

    def __init__(self, channels: int, operations: Any) -> None:
        super().__init__()
        self.norm = _normalize(operations, channels)
        self.q = operations.Conv2d(channels, channels, kernel_size=1, stride=1, padding=0)
        self.k = operations.Conv2d(channels, channels, kernel_size=1, stride=1, padding=0)
        self.v = operations.Conv2d(channels, channels, kernel_size=1, stride=1, padding=0)
        self.proj_out = operations.Conv2d(channels, channels, kernel_size=1, stride=1, padding=0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.norm(x)
        q, k, v = self.q(h), self.k(h), self.v(h)

        b, c, height, width = q.shape
        q = q.view(b, 1, c, height * width).transpose(2, 3).contiguous()
        k = k.view(b, 1, c, height * width).transpose(2, 3).contiguous()
        v = v.view(b, 1, c, height * width).transpose(2, 3).contiguous()
        out = F.scaled_dot_product_attention(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False)
        out = out.transpose(2, 3).reshape(b, c, height, width)

        out = self.proj_out(out)
        return x + out


class Downsample2D(nn.Module):
    def __init__(self, channels: int, operations: Any) -> None:
        super().__init__()
        self.conv = operations.Conv2d(channels, channels, kernel_size=3, stride=2, padding=0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.pad(x, (0, 1, 0, 1), mode="constant", value=0)
        return self.conv(x)


class Upsample2D(nn.Module):
    def __init__(self, channels: int, operations: Any) -> None:
        super().__init__()
        self.conv = operations.Conv2d(channels, channels, kernel_size=3, stride=1, padding=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.interpolate(x, scale_factor=2.0, mode="nearest")
        return self.conv(x)


class _Level(nn.Module):
    """Container so submodule names match the checkpoint's ``block``/``attn``/
    ``downsample``/``upsample`` layout (plain ``nn.Module`` attrs, ComfyUI-style)."""


class Encoder2D(nn.Module):
    def __init__(self, *, in_channels: int, z_channels: int, operations: Any) -> None:
        super().__init__()
        self.conv_in = operations.Conv2d(in_channels, _CH, kernel_size=3, stride=1, padding=1)

        curr_res = _RESOLUTION
        in_ch_mult = (1,) + _CH_MULT
        self.down = nn.ModuleList()
        block_in = _CH
        for i_level in range(len(_CH_MULT)):
            level = _Level()
            block_in = _CH * in_ch_mult[i_level]
            block_out = _CH * _CH_MULT[i_level]
            blocks = nn.ModuleList()
            for _ in range(_NUM_RES_BLOCKS):
                blocks.append(ResnetBlock2D(in_channels=block_in, out_channels=block_out, operations=operations))
                block_in = block_out
            level.block = blocks
            level.attn = nn.ModuleList()  # empty: no attention outside the bottleneck
            if i_level != len(_CH_MULT) - 1:
                level.downsample = Downsample2D(block_in, operations)
                curr_res //= 2
            self.down.append(level)

        self.mid = _Level()
        self.mid.block_1 = ResnetBlock2D(in_channels=block_in, out_channels=block_in, operations=operations)
        self.mid.attn_1 = AttnBlock2D(block_in, operations)
        self.mid.block_2 = ResnetBlock2D(in_channels=block_in, out_channels=block_in, operations=operations)

        self.norm_out = _normalize(operations, block_in)
        # double_z=True: conv_out emits mean+logvar (2 * z_channels).
        self.conv_out = operations.Conv2d(block_in, 2 * z_channels, kernel_size=3, stride=1, padding=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.conv_in(x)
        for level in self.down:
            for block in level.block:
                h = block(h)
            if hasattr(level, "downsample"):
                h = level.downsample(h)
        h = self.mid.block_1(h)
        h = self.mid.attn_1(h)
        h = self.mid.block_2(h)
        h = self.norm_out(h)
        h = F.silu(h)
        return self.conv_out(h)


class Decoder2D(nn.Module):
    def __init__(self, *, out_channels: int, z_channels: int, operations: Any) -> None:
        super().__init__()
        block_in = _CH * _CH_MULT[-1]
        self.conv_in = operations.Conv2d(z_channels, block_in, kernel_size=3, stride=1, padding=1)

        self.mid = _Level()
        self.mid.block_1 = ResnetBlock2D(in_channels=block_in, out_channels=block_in, operations=operations)
        self.mid.attn_1 = AttnBlock2D(block_in, operations)
        self.mid.block_2 = ResnetBlock2D(in_channels=block_in, out_channels=block_in, operations=operations)

        # Checkpoint keys are "up.{i_level}." with i_level=0 the *finest*
        # stage (matching diffusers_convert's up.{3-i} <-> up_blocks.{i} map:
        # hf up_blocks go coarse->fine in index order, so hf index i=0 lands
        # on ldm up.3, the coarsest). Build coarse-to-fine (i_level descending,
        # so `block_in` threads through correctly) but slot each level into
        # its checkpoint-matching index so `self.up[i_level]` is addressable
        # directly.
        num_levels = len(_CH_MULT)
        up_levels: list[_Level | None] = [None] * num_levels
        for i_level in reversed(range(num_levels)):
            level = _Level()
            block_out = _CH * _CH_MULT[i_level]
            blocks = nn.ModuleList()
            for _ in range(_NUM_RES_BLOCKS + 1):
                blocks.append(ResnetBlock2D(in_channels=block_in, out_channels=block_out, operations=operations))
                block_in = block_out
            level.block = blocks
            level.attn = nn.ModuleList()
            if i_level != 0:
                level.upsample = Upsample2D(block_in, operations)
            up_levels[i_level] = level
        self.up = nn.ModuleList(up_levels)

        self.norm_out = _normalize(operations, block_in)
        self.conv_out = operations.Conv2d(block_in, out_channels, kernel_size=3, stride=1, padding=1)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        h = self.conv_in(z)
        h = self.mid.block_1(h)
        h = self.mid.attn_1(h)
        h = self.mid.block_2(h)
        # Execute coarse-to-fine: i_level descending (mirrors training/build order).
        for i_level in reversed(range(len(self.up))):
            level = self.up[i_level]
            for block in level.block:
                h = block(h)
            if hasattr(level, "upsample"):
                h = level.upsample(h)
        h = self.norm_out(h)
        h = F.silu(h)
        return self.conv_out(h)


class AutoEncoder2D(NativeArchModule):
    """Flux/Flux2 2D image VAE. ``encode``/``decode`` operate on pixels in
    ``[-1, 1]`` (caller's responsibility, no internal ``*2-1`` rescale) and
    latents in the model-native (unscaled) space -- callers apply
    ``latent_format`` scale/shift themselves (see ``detect/registry.py``).
    """

    def __init__(
        self,
        *,
        vae_type: str,
        in_channels: int,
        out_channels: int,
        z_channels: int,
        has_quant_conv: bool,
        has_batchnorm: bool,
        operations: Any,
    ) -> None:
        super().__init__()
        self.vae_type = vae_type
        self.z_channels = z_channels
        self.has_quant_conv = has_quant_conv
        self.has_batchnorm = has_batchnorm

        self.encoder = Encoder2D(in_channels=in_channels, z_channels=z_channels, operations=operations)
        self.decoder = Decoder2D(out_channels=out_channels, z_channels=z_channels, operations=operations)

        if has_quant_conv:
            # embed_dim == z_channels for both known checkpoints (verified:
            # flux2-vae's quant_conv/post_quant_conv are square 32<->32).
            embed_dim = z_channels
            self.quant_conv = operations.Conv2d(2 * z_channels, 2 * embed_dim, kernel_size=1)
            self.post_quant_conv = operations.Conv2d(embed_dim, z_channels, kernel_size=1)
        else:
            self.quant_conv = None
            self.post_quant_conv = None

        if has_batchnorm:
            # Not built from `operations`: this is a plain running-stats
            # buffer container (affine=False), not a cast-on-forward weight
            # layer -- matches ComfyUI's AutoencodingEngineLegacy exactly.
            bn_channels = (_BN_PACK_FACTOR ** 2) * z_channels
            self.bn = nn.BatchNorm2d(
                bn_channels, eps=_BN_EPS, momentum=_BN_MOMENTUM, affine=False, track_running_stats=True,
            )
            self.bn.eval()
        else:
            self.bn = None

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "AutoEncoder2D":
        return cls(
            vae_type=config["vae_type"],
            in_channels=config["in_channels"],
            out_channels=config["out_channels"],
            z_channels=config["latent_channels"],
            has_quant_conv=config["has_quant_conv"],
            has_batchnorm=config["has_batchnorm"],
            operations=operations,
        )

    def post_load(self) -> None:
        # No computed buffers here (conv/attn VAE, no RoPE/causal masks) --
        # the batchnorm running stats are loaded weights, not derived. This
        # is the documented no-op the loading contract requires.
        if self.bn is not None:
            self.bn.eval()

    def _regularize(self, parameters: torch.Tensor) -> torch.Tensor:
        # DiagonalGaussianDistribution.mode() (deterministic: ComfyUI's
        # default regularizer construction uses sample=False).
        mean, _logvar = torch.chunk(parameters, 2, dim=1)
        return mean

    def encode(self, pixels: torch.Tensor) -> torch.Tensor:
        z = self.encoder(pixels)
        if self.has_quant_conv:
            z = self.quant_conv(z)
        z = self._regularize(z)
        if self.has_batchnorm:
            z = F.pixel_unshuffle(z, _BN_PACK_FACTOR)
            z = F.batch_norm(
                z,
                self.bn.running_mean.to(dtype=z.dtype, device=z.device),
                self.bn.running_var.to(dtype=z.dtype, device=z.device),
                weight=None,
                bias=None,
                training=False,
                momentum=_BN_MOMENTUM,
                eps=_BN_EPS,
            )
        return z

    def decode(self, latent: torch.Tensor) -> torch.Tensor:
        z = latent
        if self.has_batchnorm:
            std = torch.sqrt(
                self.bn.running_var.view(1, -1, 1, 1).to(dtype=z.dtype, device=z.device) + _BN_EPS
            )
            mean = self.bn.running_mean.view(1, -1, 1, 1).to(dtype=z.dtype, device=z.device)
            z = z * std + mean
            z = F.pixel_shuffle(z, _BN_PACK_FACTOR)
        if self.has_quant_conv:
            z = self.post_quant_conv(z)
        return self.decoder(z)


# --- Latent scale/shift (verified against ComfyUI's latent_formats.py) -----
# Kept here (not just in the registry) so the VAE module is self-documenting;
# the `detect/registry.py` arch registry is the source of truth actually consumed
# by the sampler.
LATENT_SCALE_SHIFT = {
    "flux_ae": {"scale_factor": 0.3611, "shift_factor": 0.1159},
    "flux2_ae": {"scale_factor": 1.0, "shift_factor": 0.0},
}
