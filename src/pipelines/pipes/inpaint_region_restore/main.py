"""
Inpaint Region Restore Pipe - Paste cropped inpainted region back into original image
Based on Fooocus InpaintWorker.post_process implementation
"""
from typing import Dict, Any, List
import numpy as np
from PIL import Image

from src.pipelines.contracts import BasePipe, logger
from src.platform.runtime.tensors import pil_to_numpy_rgb, numpy_to_pil
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.pipes._shared.inpainting.region_utils import resample_image
from src.pipelines.pipes._shared.inpainting.mask_utils import color_correction


class InpaintRegionRestorePipe(BasePipe):
    name = "inpaint_region_restore"
    description = "Restore inpainted region back into original full image (Fooocus-style)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "color_correction": True,  # Apply color correction for smooth blending
        }

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable
    ) -> PipeOutput:
        inpainted_image = pipe_input.input["inpainted_image"]
        crop_region = pipe_input.input["crop_region"]
        original_image = pipe_input.input["original_image"]

        # Full-size mask for color correction (Fooocus-style)
        original_mask = pipe_input.input.get("original_mask", None)

        apply_color_correction = self.config.get("color_correction", True)

        logger.info(f"[INPAINT RESTORE] Restoring inpainted region to full image")

        # Convert to numpy arrays
        if isinstance(inpainted_image, Image.Image):
            inpainted_np = pil_to_numpy_rgb(inpainted_image)
        else:
            inpainted_np = inpainted_image

        if isinstance(original_image, Image.Image):
            original_np = pil_to_numpy_rgb(original_image)
        else:
            original_np = original_image

        # Extract crop region info
        a = crop_region["top"]
        b = crop_region["bottom"]
        c = crop_region["left"]
        d = crop_region["right"]
        original_H = crop_region["original_height"]
        original_W = crop_region["original_width"]

        crop_height = b - a
        crop_width = d - c

        logger.debug(f"[INPAINT RESTORE] Crop region: [{a}:{b}, {c}:{d}] -> resizing to {crop_width}x{crop_height}")

        # Resize inpainted image back to original crop dimensions
        content = resample_image(inpainted_np, crop_width, crop_height)

        # Create result image starting with original
        result = original_np.copy()

        # Paste inpainted region back
        result[a:b, c:d] = content

        # Apply color correction for smooth blending (Fooocus-style)
        if apply_color_correction and original_mask is not None:
            logger.debug(f"[INPAINT RESTORE] Applying Fooocus-style color correction with full-size mask")

            # Convert full-size mask to numpy
            if isinstance(original_mask, Image.Image):
                mask_np = np.array(original_mask.convert('L'))
            else:
                mask_np = original_mask

            # Ensure mask matches original image size
            if mask_np.shape[:2] != original_np.shape[:2]:
                logger.debug(f"[INPAINT RESTORE] Resizing mask from {mask_np.shape[:2]} to {original_np.shape[:2]}")
                mask_pil = numpy_to_pil(mask_np)
                mask_pil = mask_pil.resize((original_W, original_H), Image.LANCZOS)
                mask_np = np.array(mask_pil)

            # Apply color correction using full-size mask (Fooocus approach)
            result = color_correction(result, original_np, mask_np)

        final_H, final_W = result.shape[:2]
        logger.info(f"[INPAINT RESTORE] Final restored image size: {final_W}x{final_H}")

        # Return as array to match generator output format
        return PipeOutput(output={
            "image": [numpy_to_pil(result)],
        })

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("inpainted_image", IOType.IMAGE, True, "Inpainted cropped image", is_array=False),
            PipeInputSpec("crop_region", IOType.DICT, True, "Crop region metadata from crop pipe", is_array=False),
            PipeInputSpec("original_image", IOType.IMAGE, True, "Original full image", is_array=False),
            PipeInputSpec("original_mask", IOType.IMAGE, False, "Original full-size mask for color correction (Fooocus-style)", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Full image with inpainted region restored", is_array=True),
        ]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="color_correction",
                param_type=bool,
                default=True,
                description="Apply color correction for smooth blending",
                required=False
            ),
        ]
