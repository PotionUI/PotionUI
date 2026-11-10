# Derived from: comfy/ldm/minimax/vae.py (ComfyUI, GPL-3.0) — H3ImageVAE / MiniMaxH3ImageVAE: the H3 encoder paired with a single-frame 2D ViT decoder (16 heads, 2D RoPE over half of each head, Conv2d post-quant conv, zeros as the trailing decoder token). Its tile stitching follows MiniMaxH3VideoVAE.tiled_decode: each tile blends against neighbours that are already blended.
from __future__ import annotations

from typing import Any

import torch

from .minimax_h3_video import (
    DECODER_ATTENTION_HEAD_DIM,
    DECODER_FFN_MULT,
    DECODER_NORM_EPS,
    DECODER_NUM_REGISTER_TOKENS,
    DECODER_ROPE_THETA,
    LATENT_CHANNELS,
    MiniMaxH3VideoVAE,
)

LATENTS_MEAN: tuple[float, ...] = (
    0.858090341091156, -0.9606591463088989, 1.0661640167236328, -0.5090325474739075,
    -0.2727581858634949, -1.3675414323806763, -0.2553254961967468, -0.26907554268836975,
    -0.5376840829849243, -0.0464097298681736, 0.6657370328903198, 0.19690127670764923,
    -0.5460608005523682, -0.4035342037677765, -0.23683024942874908, 0.25928452610969543,
    -0.30133944749832153, 0.211341992020607, -1.1206848621368408, 0.3581933379173279,
    -0.04225143790245056, 0.2604829967021942, 0.22864092886447906, 0.7056031823158264,
)
LATENTS_STD: tuple[float, ...] = (
    1.2223774194717407, 1.2767263650894165, 1.6831774711608887, 1.7549455165863037,
    1.5636216402053833, 2.194143533706665, 0.9653137922286987, 1.0569885969161987,
    0.841948926448822, 0.7729952931404114, 1.8955937623977661, 0.946841835975647,
    0.7996809482574463, 0.44988900423049927, 0.7197399735450745, 0.6936293244361877,
    2.961095094680786, 2.7694199085235596, 3.0496184825897217, 2.1088054180145264,
    3.276226282119751, 3.1627357006073, 2.2816812992095947, 2.6127843856811523,
)
PIXEL_MEAN: tuple[float, ...] = (0.485, 0.456, 0.406)
PIXEL_STD: tuple[float, ...] = (0.229, 0.224, 0.225)

IMAGE_DECODER_NUM_LAYERS = 24
IMAGE_DECODER_NUM_ATTENTION_HEADS = 16
IMAGE_DECODER_ROPE_DIM_RATIO = 0.5
IMAGE_DECODER_ROPE_AXES = 2
IMAGE_DECODER_PATCH_SIZE_T = 1
IMAGE_DECODER_OUT_ROWS = 3 * 16 * 16


class MiniMaxH3ImageVAE(MiniMaxH3VideoVAE):
    working_dtypes: tuple[torch.dtype, ...] = (torch.float16, torch.float32)

    def __init__(
        self, *,
        latent_channels: int = LATENT_CHANNELS,
        decoder_num_layers: int = IMAGE_DECODER_NUM_LAYERS,
        decoder_num_attention_heads: int = IMAGE_DECODER_NUM_ATTENTION_HEADS,
        decoder_attention_head_dim: int = DECODER_ATTENTION_HEAD_DIM,
        operations: Any,
    ) -> None:
        super().__init__(
            latent_channels=latent_channels,
            decoder_num_layers=decoder_num_layers,
            decoder_num_attention_heads=decoder_num_attention_heads,
            decoder_attention_head_dim=decoder_attention_head_dim,
            decoder_num_register_tokens=DECODER_NUM_REGISTER_TOKENS,
            decoder_ffn_mult=DECODER_FFN_MULT,
            decoder_rope_theta=DECODER_ROPE_THETA,
            decoder_rope_dim_ratio=IMAGE_DECODER_ROPE_DIM_RATIO,
            decoder_norm_eps=DECODER_NORM_EPS,
            clip_length=1,
            token_drop=0,
            operations=operations,
            decoder_patch_size_t=IMAGE_DECODER_PATCH_SIZE_T,
            decoder_rope_axes=IMAGE_DECODER_ROPE_AXES,
            decoder_zero_suffix_token=True,
        )
        self.post_quant_conv = operations.Conv2d(latent_channels, latent_channels, kernel_size=1)
        del self.latents_mean
        del self.latents_std
        self.register_buffer("latents_mean", torch.tensor(LATENTS_MEAN, dtype=torch.float32), persistent=False)
        self.register_buffer("latents_std", torch.tensor(LATENTS_STD, dtype=torch.float32), persistent=False)

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "MiniMaxH3ImageVAE":
        return cls(
            latent_channels=config.get("latent_channels", LATENT_CHANNELS),
            decoder_num_layers=config.get("decoder_num_layers", IMAGE_DECODER_NUM_LAYERS),
            decoder_num_attention_heads=config.get("decoder_num_attention_heads", IMAGE_DECODER_NUM_ATTENTION_HEADS),
            decoder_attention_head_dim=config.get("decoder_attention_head_dim", DECODER_ATTENTION_HEAD_DIM),
            operations=operations,
        )

    def post_load(self) -> None:
        super().post_load()
        device = self.latents_mean.device if self.latents_mean.device.type != "meta" else torch.device("cpu")
        self.latents_mean = torch.tensor(LATENTS_MEAN, dtype=torch.float32, device=device)
        self.latents_std = torch.tensor(LATENTS_STD, dtype=torch.float32, device=device)

    def _decode_pixels(self, z: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.post_quant_conv(z.squeeze(2)).unsqueeze(2))

    def _stitch_decoded_tiles(
        self, tiles: list[list[torch.Tensor]], height_overlaps: list[int], width_overlaps: list[int],
    ) -> torch.Tensor:
        starts = [0]
        for j, tile in enumerate(tiles[0][:-1]):
            starts.append(starts[-1] + tile.shape[-1] - width_overlaps[j])
        strip = None
        bands = []
        for i, row in enumerate(tiles):
            left_tail = None
            pieces = []
            for j, tile in enumerate(row):
                if i > 0:
                    above = strip[..., :, starts[j] : starts[j] + tile.shape[-1]]
                    tile = self._blend(above, tile, height_overlaps[i - 1], dim=-2)
                if j > 0:
                    tile = self._blend(left_tail, tile, width_overlaps[j - 1], dim=-1)
                if j < len(row) - 1:
                    left_tail = tile[..., :, -width_overlaps[j]:]
                    tile = tile[..., :, : -width_overlaps[j]]
                pieces.append(tile)
            band = torch.cat(pieces, dim=-1)
            if i < len(tiles) - 1:
                strip = band[..., -height_overlaps[i]:, :]
                band = band[..., : -height_overlaps[i], :]
            bands.append(band)
        return torch.cat(bands, dim=-2)

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        if z.ndim != 5 or z.shape[2] != 1:
            raise ValueError(f"the MiniMax-H3-Image VAE decodes single-frame [B, C, 1, H, W] latents, got {list(z.shape)}")
        return self._decode_clip(z)

    def normalize(self, latent: torch.Tensor) -> torch.Tensor:
        view = (1, -1, 1, 1, 1)
        mean = self.latents_mean.to(device=latent.device, dtype=torch.float32).view(view)
        std = self.latents_std.to(device=latent.device, dtype=torch.float32).view(view)
        return (latent.to(torch.float32) - mean) / std

    def denormalize(self, latent: torch.Tensor) -> torch.Tensor:
        view = (1, -1, 1, 1, 1)
        mean = self.latents_mean.to(device=latent.device, dtype=torch.float32).view(view)
        std = self.latents_std.to(device=latent.device, dtype=torch.float32).view(view)
        return latent.to(torch.float32) * std + mean


def pixels_to_vae_input(pixels: torch.Tensor) -> torch.Tensor:
    view = (1, -1, 1, 1, 1)
    mean = torch.tensor(PIXEL_MEAN, dtype=torch.float32, device=pixels.device).view(view)
    std = torch.tensor(PIXEL_STD, dtype=torch.float32, device=pixels.device).view(view)
    return ((pixels.to(torch.float32) + 1.0) * 0.5 - mean) / std


def vae_output_to_unit(decoded: torch.Tensor) -> torch.Tensor:
    view = (1, -1, 1, 1, 1)
    mean = torch.tensor(PIXEL_MEAN, dtype=torch.float32, device=decoded.device).view(view)
    std = torch.tensor(PIXEL_STD, dtype=torch.float32, device=decoded.device).view(view)
    return (decoded.to(torch.float32) * std + mean).clamp(0.0, 1.0)
