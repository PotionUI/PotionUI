"""
Inpaint Region Crop Pipe - Fooocus-style cropping to masked region
Crops the image to the masked region with padding to reduce VRAM usage
Based on Fooocus InpaintWorker implementation
"""
from typing import Dict, Any, List
import numpy as np
from PIL import Image

from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.platform.util.dimensions import floor_to_multiple
from src.platform.runtime.tensors import pil_to_numpy_rgb, numpy_to_pil
from src.pipelines.pipes._shared.inpainting.region_utils import (
    compute_initial_abcd,
    solve_abcd,
    get_image_shape_ceil,
    set_image_shape_ceil,
)


class InpaintRegionCropPipe(BasePipe):
    name = "inpaint_region_crop"
    description = "Crop image to masked region for memory-efficient inpainting (Fooocus-style)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "k": 0.618,  # Fooocus default padding factor
            "min_size": 1024,  # Minimum size to upscale to
            "max_size": 1024,  # Maximum size to downscale to
        }

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable
    ) -> PipeOutput:
        image = pipe_input.input["image"]
        mask = pipe_input.input["mask"]

        k = float(self.config.get("k", 0.618))
        min_size = int(self.config.get("min_size", 1024))
        max_size = int(self.config.get("max_size", 1024))

        logger.debug(f"[INPAINT CROP] Starting region crop with k={k}, min_size={min_size}, max_size={max_size}")

        # Convert to numpy arrays
        if isinstance(image, Image.Image):
            image_np = pil_to_numpy_rgb(image)
        else:
            image_np = image

        if isinstance(mask, Image.Image):
            mask_np = np.array(mask.convert('L'))
        else:
            mask_np = mask

        original_H, original_W = image_np.shape[:2]
        logger.debug(f"[INPAINT CROP] Original image size: {original_W}x{original_H}")

        # Ensure mask matches image size
        if mask_np.shape[:2] != image_np.shape[:2]:
            mask_pil = numpy_to_pil(mask_np)
            mask_pil = mask_pil.resize((original_W, original_H), Image.LANCZOS)
            mask_np = np.array(mask_pil)

        # Compute bounding box around masked region
        a, b, c, d = compute_initial_abcd(mask_np)
        a, b, c, d = solve_abcd(mask_np, a, b, c, d, k=k)

        # Ensure crop dimensions are divisible by 8 (required for diffusion models)
        crop_h = b - a
        crop_w = d - c
        new_crop_h = floor_to_multiple(crop_h)
        new_crop_w = floor_to_multiple(crop_w)

        # Adjust bounds to center the divisible-by-8 crop
        h_diff = crop_h - new_crop_h
        w_diff = crop_w - new_crop_w
        a += h_diff // 2
        b = a + new_crop_h
        c += w_diff // 2
        d = c + new_crop_w

        # Clamp to image bounds
        a = max(0, min(original_H, a))
        b = max(0, min(original_H, b))
        c = max(0, min(original_W, c))
        d = max(0, min(original_W, d))

        logger.debug(f"[INPAINT CROP] Crop region (aligned to 8px): [{a}:{b}, {c}:{d}] (y: {b-a}px, x: {d-c}px)")

        # Crop to interested region
        cropped_image = image_np[a:b, c:d]
        cropped_mask = mask_np[a:b, c:d]

        # Upscale if too small
        shape_ceil = get_image_shape_ceil(cropped_image)
        if shape_ceil < min_size:
            logger.debug(f"[INPAINT CROP] Upscaling from {shape_ceil}px to {min_size}px")
            cropped_image = set_image_shape_ceil(cropped_image, min_size)
            # Also resize mask
            mask_pil = numpy_to_pil(cropped_mask)
            mask_pil = mask_pil.resize((cropped_image.shape[1], cropped_image.shape[0]), Image.LANCZOS)
            cropped_mask = np.array(mask_pil)

        # Downscale if too large
        shape_ceil = get_image_shape_ceil(cropped_image)
        if shape_ceil > max_size:
            logger.debug(f"[INPAINT CROP] Downscaling from {shape_ceil}px to {max_size}px")
            cropped_image = set_image_shape_ceil(cropped_image, max_size)
            # Also resize mask
            mask_pil = numpy_to_pil(cropped_mask)
            mask_pil = mask_pil.resize((cropped_image.shape[1], cropped_image.shape[0]), Image.LANCZOS)
            cropped_mask = np.array(mask_pil)

        final_H, final_W = cropped_image.shape[:2]
        logger.info(f"[INPAINT CROP] Final cropped size: {final_W}x{final_H} (VRAM savings: {(original_H*original_W)/(final_H*final_W):.1f}x)")

        # Store crop region for restore pipe
        crop_region = {
            "top": a,
            "bottom": b,
            "left": c,
            "right": d,
            "original_width": original_W,
            "original_height": original_H,
        }

        logger.debug(f"[INPAINT CROP] Storing original full-size mask for color correction")

        return PipeOutput(output={
            "image": numpy_to_pil(cropped_image),
            "mask": numpy_to_pil(cropped_mask),
            "crop_region": crop_region,
            "original_image": numpy_to_pil(image_np),
            "original_mask": numpy_to_pil(mask_np),  # Full-size mask for restore pipe
        })

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Source image for inpainting", is_array=False),
            PipeInputSpec("mask", IOType.IMAGE, True, "Preprocessed mask", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Cropped image (masked region only)", is_array=False),
            PipeOutputSpec("mask", IOType.IMAGE, "Cropped mask", is_array=False),
            PipeOutputSpec("crop_region", IOType.DICT, "Crop region metadata for restore", is_array=False),
            PipeOutputSpec("original_image", IOType.IMAGE, "Original full image for restore", is_array=False),
            PipeOutputSpec("original_mask", IOType.IMAGE, "Original full-size mask for color correction", is_array=False),
        ]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="k",
                param_type=float,
                default=0.618,
                description="Padding factor (0.618 = Fooocus default)",
                required=False
            ),
            PipeConfigSpec(
                name="min_size",
                param_type=int,
                default=1024,
                description="Minimum size to upscale to",
                required=False
            ),
            PipeConfigSpec(
                name="max_size",
                param_type=int,
                default=1024,
                description="Maximum size to downscale to",
                required=False
            ),
        ]
