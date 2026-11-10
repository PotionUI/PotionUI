from __future__ import annotations

import logging
from typing import Any

import numpy as np
import torch

from src.pipelines.pipes._shared.generation.native_generator import register_native_generator
from src.pipelines.pipes._shared.vae.minimax_h3_decode import plan_tile_batch
from src.pipelines.pipes.generator.minimax_h3_image.references import as_rgb
from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.errors import retry_after_cuda_oom
from src.platform.runtime.native.vae.minimax_h3_image import pixels_to_vae_input, vae_output_to_unit

logger = logging.getLogger(__name__)

SPATIAL_DOWNSCALE = 16
REFERENCE_ENCODE_SEED = 42


@register_native_generator("minimax_h3_image")
class MiniMaxH3ImageNativeGenerator(NativeGenerator):
    model_sigma_dtype = torch.float32
    latent_output_dtype = torch.float32

    def _spatial_downscale(self) -> int:
        return SPATIAL_DOWNSCALE

    def latent_shape_for(self, width: int, height: int, batch: int = 1) -> tuple[int, ...]:
        channels = int((self.spec.latent_format or {}).get("latent_channels", 24))
        return (batch, channels, 1, height // SPATIAL_DOWNSCALE, width // SPATIAL_DOWNSCALE)

    @torch.no_grad()
    def encode_reference(self, image: Any) -> torch.Tensor:
        device = self.device_plan.vae_device
        self.vae.move_to(device)
        module = self.vae.module
        array = np.asarray(as_rgb(image), dtype=np.float32) / 127.5 - 1.0
        pixels = torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0).unsqueeze(2).to(device)
        generator = torch.Generator("cpu").manual_seed(REFERENCE_ENCODE_SEED)
        latent = module.encode(
            pixels_to_vae_input(pixels).to(self.vae.compute_dtype), sample_posterior=True, generator=generator,
        )
        return module.normalize(latent).cpu()

    def _decode_pixels(self, latents: torch.Tensor, device: str, batch: int | None = None) -> np.ndarray:
        module = self.vae.module
        z = module.denormalize(latents.to(device))
        module.decode_tile_batch_size = batch or plan_tile_batch(module, z, str(device))["batch_size"]
        unit = vae_output_to_unit(module.decode(z.to(self.vae.compute_dtype)))[:, :, 0]
        return (unit * 255.0).round().to(torch.uint8).permute(0, 2, 3, 1).cpu().numpy()

    @torch.no_grad()
    def decode(self, latents: torch.Tensor, *, vram_free_gb: float | None = None) -> np.ndarray:
        device = self.device_plan.vae_device
        if not self._resident("dit"):
            self.dit.offload()
        self.vae.move_to(device)
        try:
            return self._decode_pixels(latents, device)
        except RuntimeError as exc:
            if not retry_after_cuda_oom(exc):
                raise
            logger.warning("MiniMax-H3-Image decode ran out of memory; retrying with the DiT offloaded")
            self.dit.offload()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            return self._decode_pixels(latents, device, batch=1)
