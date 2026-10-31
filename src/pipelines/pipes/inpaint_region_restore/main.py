"""
Inpaint Region Restore Pipe - Paste cropped inpainted region back into original image
Based on Fooocus InpaintWorker.post_process implementation
"""
from typing import List
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
from src.pipelines.pipes._shared.inpainting.mask_utils import color_correction, composite_inpaint, composite_region


class InpaintRegionRestorePipe(BasePipe):
    name = "inpaint_region_restore"
    description = "Restore inpainted region back into original full image (Fooocus-style)"

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable
    ) -> PipeOutput:
        inpainted_image = pipe_input.input["inpainted_image"]
        composite = self.config.get("strategy", "fooocus") in ("composite", "inpaint")
        if isinstance(inpainted_image, (list, tuple)):
            generated = list(inpainted_image) if composite else list(inpainted_image[:1])
            inpainted_image = generated[0]
        else:
            generated = [inpainted_image]
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

        if composite:
            if original_mask is None:
                raise ValueError("inpaint_region_restore with strategy composite needs original_mask")
            return self._composite(original_np, generated, original_mask, crop_region)

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

    def _composite(self, original_np, generated, original_mask, crop_region) -> PipeOutput:
        top, bottom, left, right = (crop_region[k] for k in ("top", "bottom", "left", "right"))
        if isinstance(original_mask, Image.Image):
            mask_np = np.array(original_mask.convert("L"))
        else:
            mask_np = original_mask
        if mask_np.shape[:2] != original_np.shape[:2]:
            mask_np = np.array(
                numpy_to_pil(mask_np).resize((original_np.shape[1], original_np.shape[0]), Image.BILINEAR)
            )
        images = []
        for item in generated:
            item_np = pil_to_numpy_rgb(item) if isinstance(item, Image.Image) else item
            content = resample_image(item_np, right - left, bottom - top)
            result = original_np.copy()
            extra = (int(self.config.get("feather_px", 0) or 0),) if self.config.get("strategy") == "inpaint" else ()
            blend = composite_inpaint if self.config.get("strategy") == "inpaint" else composite_region
            result[top:bottom, left:right] = blend(
                original_np[top:bottom, left:right],
                content,
                mask_np[top:bottom, left:right],
                float(self.config.get("feather", 0.08)),
                bool(self.config.get("color_match", True)),
                *extra,
            )
            images.append(numpy_to_pil(result))
        return PipeOutput(output={"image": images})

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("inpainted_image", IOType.IMAGE, True, "Inpainted cropped image", is_array=True),
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
                name="strategy",
                param_type=str,
                default="fooocus",
                description="fooocus pastes the crop and blends by the mask; inpaint feathers inward from the region edge and removes the tone step against the surroundings; composite feathers in proportion to the region, matches colour to its surroundings and leaves every pixel outside the feather untouched",
                required=False,
                choices=["fooocus", "composite", "inpaint"]
            ),
            PipeConfigSpec(
                name="feather",
                param_type=float,
                default=0.08,
                description="Composite strategy: feather width as a fraction of the region's longer side",
                required=False,
                min_value=0.0,
                max_value=1.0
            ),
            PipeConfigSpec(
                name="feather_px",
                param_type=int,
                default=0,
                description="Inpaint strategy: feather width in pixels, replacing the fraction when above 0",
                required=False,
                min_value=0,
                max_value=1024
            ),
            PipeConfigSpec(
                name="color_match",
                param_type=bool,
                default=True,
                description="Composite strategy: match the edit's colour statistics to a ring around the region",
                required=False
            ),
            PipeConfigSpec(
                name="color_correction",
                param_type=bool,
                default=True,
                description="Apply color correction for smooth blending",
                required=False
            ),
        ]
