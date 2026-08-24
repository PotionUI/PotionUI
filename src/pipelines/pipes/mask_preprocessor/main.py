"""
Mask Preprocessor Pipe - Fooocus-style mask preprocessing
Dilates and blurs the mask for better inpainting results
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
from src.platform.util.inpaint import blur_mask, dilate_mask


class MaskPreprocessorPipe(BasePipe):
    name = "mask_preprocessor"
    description = "Preprocess inpainting mask with dilation and blur (Fooocus-style)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "dilate_iterations": 4,  # Fooocus default
            "blur_radius": 4,        # Gaussian blur radius
        }

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable
    ) -> PipeOutput:
        mask = pipe_input.input["mask"]

        dilate_iterations = int(self.config.get("dilate_iterations", 4))
        blur_radius = int(self.config.get("blur_radius", 4))

        logger.info(f"[MASK PREPROCESSOR] Processing mask - dilate: {dilate_iterations}, blur: {blur_radius}")

        # Convert PIL Image to numpy array
        if isinstance(mask, Image.Image):
            mask_np = np.array(mask.convert('L'))  # Convert to grayscale
        elif isinstance(mask, np.ndarray):
            mask_np = mask
        else:
            raise ValueError(f"Unsupported mask type: {type(mask)}")

        logger.debug(f"[MASK PREPROCESSOR] Original mask shape: {mask_np.shape}, dtype: {mask_np.dtype}")

        # Apply dilation
        if dilate_iterations > 0:
            mask_np = dilate_mask(mask_np, iterations=dilate_iterations)
            logger.debug(f"[MASK PREPROCESSOR] Applied {dilate_iterations} dilation iterations")

        # Apply blur
        if blur_radius > 0:
            mask_np = blur_mask(mask_np, blur_radius=blur_radius)
            logger.debug(f"[MASK PREPROCESSOR] Applied Gaussian blur with radius {blur_radius}")

        # Convert back to PIL Image
        mask_processed = Image.fromarray(mask_np)
        logger.debug(f"[MASK PREPROCESSOR] Processed mask size: {mask_processed.size}")

        return PipeOutput(output={"mask": mask_processed})

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("mask", IOType.IMAGE, True, "Raw mask image to preprocess", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("mask", IOType.IMAGE, "Preprocessed mask (dilated and blurred)", is_array=False),
        ]

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="dilate_iterations",
                param_type=int,
                default=4,
                description="Number of dilation iterations to expand mask",
                required=False,
                min_value=0,
                max_value=20
            ),
            PipeConfigSpec(
                name="blur_radius",
                param_type=int,
                default=4,
                description="Gaussian blur radius for smooth mask edges",
                required=False,
                min_value=0,
                max_value=20
            ),
        ]
