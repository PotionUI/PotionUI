"""Tiled hi-res refiner for the native engine (upscale -> per-tile img2img).

The one-click "make it FHD/4K" pipe. Given a generated image, an optional ESRGAN
upscale model (Lanczos fallback), and a target scale, it upscales, then refines
the enlarged image tile-by-tile with a low-denoise img2img pass — recovering the
detail a plain upscale can't invent, while keeping peak VRAM bounded by ONE tile
regardless of the output resolution (the low-VRAM promise: a 4K output refines in
1024²-sized bites).

It is family-agnostic: it reuses the generation's own ``model`` bundle and
``conditioning`` and builds the family-correct ``NativeGenerator`` via the shared
factory, so Anima / Flux / Qwen / Z-Image / Krea-2 all refine through the same
pipe. The tile geometry and seam blend live in :mod:`.tiling` (pure, tested); the
per-tile model work is the shared :func:`img2img_denoise`.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from src.pipelines.outputs import (
    CompareImagesGenerationOutput,
    GalleryGenerationOutput,
    ImageGenerationOutput,
)
from src.platform.observability.logger import logger
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
)
from src.pipelines.outputs import Icon
from src.platform.runtime.native.engine import Conditioning
from src.pipelines.pipes._shared.generation.img2img import img2img_denoise
from src.pipelines.pipes._shared.generation.native_generator import build_native_generator
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter
from src.pipelines.pipes.tiled_refiner.tiling import tile_denoise, tiled_refine


def _round_to_multiple(value: int, multiple: int) -> int:
    """Nearest positive multiple of ``multiple`` (at least one multiple)."""
    return max(multiple, int(round(value / multiple)) * multiple)


class TiledRefiner(BasePipe):
    name = "tiled_refiner"
    description = "Upscale then refine an image tile-by-tile via low-denoise img2img (bounded VRAM)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "scale": 2.0,
            "upscale_model": None,
            "denoise": 0.25,
            "content_aware": True,
            "min_denoise": 0.10,
            "tile_size": 1024,
            "tile_overlap": 128,
            "steps": 20,
            "guidance": 6.0,
            "sampler": "euler",
            "seed": -1,
            "device": "cuda",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("scale", float, 2.0, "Target upscale factor", required=False,
                           min_value=1.0, max_value=8.0),
            PipeConfigSpec("upscale_model", str, None,
                           "ESRGAN .pth under models/upscalers/ (blank -> Lanczos)", required=False),
            PipeConfigSpec("denoise", float, 0.25, "Per-tile img2img denoise strength (upper bound when content-aware)",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("content_aware", bool, True,
                           "Scale per-tile denoise by texture (flat tiles refine less; anti-hallucination)",
                           required=False),
            PipeConfigSpec("min_denoise", float, 0.10, "Denoise floor for flat/low-texture tiles (content-aware)",
                           required=False, min_value=0.0, max_value=1.0),
            PipeConfigSpec("tile_size", int, 1024, "Tile working resolution (px)", required=False,
                           min_value=256, max_value=2048),
            PipeConfigSpec("tile_overlap", int, 128, "Tile overlap for feathered seams (px)", required=False,
                           min_value=0, max_value=512),
            PipeConfigSpec("steps", int, 20, "Denoising steps per tile", required=False,
                           min_value=1, max_value=100),
            PipeConfigSpec("guidance", float, 6.0, "CFG scale", required=False, min_value=0.0, max_value=30.0),
            PipeConfigSpec("sampler", str, "euler", "Sampler", required=False,
                           choices=["euler", "dpmpp_2m", "unipc"]),
            PipeConfigSpec("seed", int, -1, "Seed (-1 -> per-image random)", required=False, min_value=-1),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Images to upscale + refine", is_array=True),
            PipeInputSpec("model", IOType.MODEL, True, "Native model bundle (reused from generation)", is_array=False),
            PipeInputSpec("conditioning", IOType.CONDITIONING, True,
                          "Prompt conditioning reused per image", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Per-image seeds", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Refined hi-res images", is_array=True),
        ]

    # -- process -----------------------------------------------------------

    def process(
        self,
        pipe_input: PipeInput,
        generation_outputs: callable,
        is_cancelled: Optional[callable] = None,
    ) -> PipeOutput:
        images = pipe_input.input["image"]
        if not isinstance(images, list):
            images = [images]
        bundle = pipe_input.input["model"]
        conditioning = pipe_input.input.get("conditioning") or []
        seeds = pipe_input.input.get("seed") or []

        scale = float(self.config.get("scale", 2.0))
        upscale_model = self.config.get("upscale_model") or None
        denoise = float(self.config.get("denoise", 0.25))
        content_aware = bool(self.config.get("content_aware", True))
        min_denoise = float(self.config.get("min_denoise", 0.10))
        steps = int(self.config.get("steps", 20))
        guidance = float(self.config.get("guidance", 6.0))
        sampler = self.config.get("sampler", "euler")
        device = self.config.get("device", "cuda")
        cfg_seed = int(self.config.get("seed", -1))

        progress = ProgressEmitter(generation_outputs, title=self.name)

        generator = build_native_generator(bundle, device=device)
        granularity = generator.pixel_granularity()
        tile = _round_to_multiple(int(self.config.get("tile_size", 1024)), granularity)
        overlap = _round_to_multiple(int(self.config.get("tile_overlap", 128)), granularity)
        overlap = min(overlap, tile - granularity)  # overlap must leave a positive step

        logger.info(
            "[TILED REFINER] %s/%s: %d img, scale %.2fx, denoise %.2f%s, tile %d overlap %d (granularity %d)",
            generator.spec.family, generator.spec.variant, len(images), scale, denoise,
            f" (content-aware floor {min_denoise:.2f})" if content_aware else "",
            tile, overlap, granularity,
        )

        results: List[ImageGenerationOutput] = []
        for index, source in enumerate(images):
            if is_cancelled and is_cancelled():
                break

            src_pil = source.convert("RGB") if isinstance(source, Image.Image) else Image.fromarray(np.asarray(source)).convert("RGB")
            cond_obj = self._conditioning_for(conditioning, index)
            seed = self._seed_for(seeds, index, cfg_seed)

            enlarged = self._upscale(src_pil, scale, upscale_model, device)
            enlarged = self._snap(generator, enlarged)
            image_np = np.asarray(enlarged)

            progress.state(
                f"Refining <<RESOLUTION:{enlarged.width}x{enlarged.height}>> image {index + 1}/{len(images)}",
                icon=Icon(name="bolt", effect="pulse"),
            )

            def refine(crop: np.ndarray, _tile_index: int, _seed=seed, _cond=cond_obj) -> np.ndarray:
                # Content-aware denoise: flat/low-texture tiles refine at min_denoise so
                # ambiguous regions (sky, mist, smooth rock) can't grow phantom subjects
                # from the whole-image prompt; busy tiles get the full denoise for detail.
                tile_d = tile_denoise(crop, denoise, min_denoise) if content_aware else denoise
                return img2img_denoise(
                    generator, crop, _cond,
                    steps=steps, seed=_seed, cfg_scale=guidance, sampler=sampler,
                    denoise=tile_d, is_cancelled=is_cancelled,
                )[0]

            def on_tile(done: int, total: int) -> None:
                progress.step(done, total, state="REFINE", icon=Icon(name="bolt", effect="pulse"))

            refined_np = tiled_refine(image_np, refine, tile=tile, overlap=overlap, on_tile=on_tile)
            refined_pil = Image.fromarray(refined_np)

            generation_outputs(CompareImagesGenerationOutput(
                index=index,
                compare=("Original", src_pil),
                to=(f"Refined {refined_pil.width}x{refined_pil.height}", refined_pil),
            ))
            results.append(ImageGenerationOutput(
                image=refined_pil, temporary=False, seed=seed,
                resolution=(refined_pil.width, refined_pil.height), cfg=guidance, step=steps,
            ))

        generation_outputs(GalleryGenerationOutput(images=results))
        return PipeOutput(output={"image": [r.image for r in results]})

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _conditioning_for(conditioning: List[Any], index: int) -> Conditioning:
        cond_model = conditioning[index] if index < len(conditioning) else conditioning[-1]
        return Conditioning(cond=cond_model.embeds, uncond=cond_model.n_embeds or None)

    @staticmethod
    def _seed_for(seeds: List[Any], index: int, cfg_seed: int) -> int:
        if index < len(seeds):
            return int(seeds[index])
        if cfg_seed >= 0:
            return cfg_seed
        return random.randint(0, 2 ** 31 - 1)

    @staticmethod
    def _snap(generator, image: Image.Image) -> Image.Image:
        """Snap an image to the DiT pixel granularity so tile crops land on latent
        boundaries (a non-multiple breaks the patchify rearrange)."""
        snap_w, snap_h = generator.snap_resolution(image.width, image.height)
        if (snap_w, snap_h) != (image.width, image.height):
            return image.resize((snap_w, snap_h), Image.LANCZOS)
        return image

    @staticmethod
    def _upscale(image: Image.Image, scale: float, model_path: Optional[str], device: str) -> Image.Image:
        """Enlarge by ``scale`` — ESRGAN when a model is given, else Lanczos. ``scale``
        near 1.0 skips enlargement (refine-in-place)."""
        if scale <= 1.001:
            return image
        if model_path:
            # Lazy import: the ESRGAN arch (vendor/chainner_pfn) is heavy and only needed here.
            from src.pipelines.pipes.upscaler.main import ImageUpscaler

            upscaler = ImageUpscaler(model_path=Path(model_path), device=device, user_scale=scale)
            return upscaler.upscale(image)
        target = (round(image.width * scale), round(image.height * scale))
        logger.debug("[TILED REFINER] no upscale model -> Lanczos %sx%s -> %sx%s",
                    image.width, image.height, *target)
        return image.resize(target, Image.LANCZOS)
