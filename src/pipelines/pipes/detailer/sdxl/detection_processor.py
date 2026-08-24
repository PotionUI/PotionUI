from typing import List, Callable
from PIL import Image
from pathlib import Path

from src.pipelines.outputs import ProgressGenerationOutput, ImageGenerationOutput, CompareImagesGenerationOutput
from src.pipelines.models import Model
from src.pipelines.contracts import IOType
from src.pipelines.contracts import GenerationInput, GenerationInputItem
from src.pipelines.outputs import Icon
from src.platform.util.latents import generate_seed
from src.pipelines.pipes._shared.detection.base_detector import BaseDetector


class BaseDetectionProcessor:
    """
    Template method pattern implementation for detection processing.
    Handles the common flow: detect → visualize → extract → upscale → enhance → paste

    This eliminates ~90% of code duplication between different detection types.
    """

    def __init__(self, detector: BaseDetector, helper):
        """
        Initialize processor with a specific detector and helper utilities.

        Args:
            detector: Concrete detector implementation (FaceDetector, HandDetector, etc.)
            helper: DetailerHelper instance for utility methods
        """
        self.detector = detector
        self.helper = helper
        self.detection_type = detector.get_detection_type()
        self.config = detector.config

    def process_detection(
        self,
        image: Image.Image,
        model: Model,
        conditioning: list,
        seed: int,
        index: int,
        generation_outputs: Callable
    ) -> Image.Image:
        """
        Process a single image with the configured detector.
        This is the template method that defines the processing flow.

        Args:
            image: Input image to process
            model: Diffusion model instance (already loaded)
            conditioning: Encoded prompt conditioning
            seed: Random seed for generation
            index: Image index in batch
            generation_outputs: Callback for progress outputs

        Returns:
            Processed image with enhanced regions
        """
        # Convert to RGB
        image = image.convert("RGB")
        current_image = image.copy()

        # Step 1: Detect objects
        boxes = self.detector.detect(current_image)
        if not boxes:
            return current_image

        # Step 2: Filter boxes by size
        boxes = self.detector.filter_boxes(boxes, image.size)
        if not boxes:
            return current_image

        # Step 3: Generate progress output
        self._output_detection_progress(boxes, generation_outputs)

        # Step 4: Visualize detections
        visualization = self._visualize_detections(current_image, boxes)
        generation_outputs(ImageGenerationOutput(image=visualization))

        # Step 5: Extract and process each region
        regions = self.helper.extract_regions(current_image, boxes, self.detection_type)

        for region_img, region_mask, coords in regions:
            current_image = self._process_region(
                current_image=current_image,
                region_img=region_img,
                region_mask=region_mask,
                coords=coords,
                model=model,
                conditioning=conditioning,
                seed=seed,
                index=index,
                generation_outputs=generation_outputs
            )

        return current_image

    def _output_detection_progress(self, boxes: list, generation_outputs: Callable):
        """Output progress information about detected objects."""
        detection_type = self.detection_type
        backend_type = self.config.get("type", "yolo").upper()

        # Get model name or backend type
        if backend_type == "YOLO":
            model_name = self.config.get("model", "unknown")
            model_display = f"YOLO:{Path(model_name).stem}"
        else:
            model_display = backend_type

        generation_outputs(
            ProgressGenerationOutput(
                state=f"Detected: <<NUMBER:{len(boxes)}>> {detection_type}(s), using <<MODEL:{model_display}>>",
                icon=Icon(self._get_icon_name()),
            )
        )

    def _get_icon_name(self) -> str:
        """Get the appropriate icon for this detection type."""
        icon_map = {
            "face": "face-smile",
            "hand": "hand",
            "eyes": "eye",
            "teeth": "face-smile"
        }
        return icon_map.get(self.detection_type, "face-smile")

    def _visualize_detections(self, image: Image.Image, boxes: list) -> Image.Image:
        """Visualize detected bounding boxes."""
        return self.helper.visualize_detections(image, boxes, self.detection_type)

    def _process_region(
        self,
        current_image: Image.Image,
        region_img: Image.Image,
        region_mask: Image.Image,
        coords: tuple,
        model: Model,
        conditioning: list,
        seed: int,
        index: int,
        generation_outputs: Callable
    ) -> Image.Image:
        """
        Process a single detected region through the enhancement pipeline.

        Flow: upscale (optional) → create mask → enhance → downscale (if upscaled) → paste
        """
        original_size = region_img.size
        should_upscale = self.detector.should_upscale_region(region_img.size)

        # Optional upscaling
        if should_upscale:
            region_img, region_mask, original_size = self.helper.upscale_region(
                region_img, region_mask, self.detection_type
            )
            generation_outputs(
                ProgressGenerationOutput(
                    state=f"SDXL upscaled {self.detection_type}: <<RESOLUTION:{original_size[0]}x{original_size[1]}>> → <<RESOLUTION:{region_img.size[0]}x{region_img.size[1]}>>",
                    icon=Icon("arrow-up"),
                )
            )

        # Create detection-specific mask
        mask_box = [0, 0, *region_img.size]
        adaptive_mask = self.detector.create_mask(region_img.size, mask_box, region_img)

        # Blend with region mask
        blend_factor = self._get_mask_blend_factor()
        combined_mask = Image.blend(region_mask, adaptive_mask, blend_factor)

        # Adjust steps based on detection size
        adjusted_steps = self.detector.adjust_steps(coords[:4], current_image.size)

        # Create output handler for real-time updates
        def region_generation_output_handler(output):
            if isinstance(output, ImageGenerationOutput):
                temp_image = current_image.copy()
                display_region = output.image
                display_mask = combined_mask

                if should_upscale:
                    display_region = self.helper.downscale_region(display_region, original_size, "LANCZOS")
                    display_mask = self.helper.downscale_region(display_mask, original_size, "LANCZOS")

                temp_image.paste(display_region, coords[:2], mask=display_mask)
                generation_outputs(ImageGenerationOutput(image=temp_image))
            else:
                generation_outputs(output)

        # Enhance region using model
        from src.platform.observability.logger import logger
        logger.debug(f"[DETAILER] Input region: size={region_img.size}, mode={region_img.mode}")
        logger.debug(f"[DETAILER] Input mask: size={combined_mask.size}, mode={combined_mask.mode}")

        enhanced_region = model.img2img(
            generation_input=GenerationInput(input=[
                GenerationInputItem(name="image", value=region_img, io_type=IOType.IMAGE),
                # GenerationInputItem(name="mask", value=combined_mask, io_type=IOType.MASK),
                GenerationInputItem(name="denoise", value=float(self.config.get("strength", 0.12)), io_type=IOType.DENOISE),
                GenerationInputItem(name="resolution", value=region_img.size, io_type=IOType.RESOLUTION),
                GenerationInputItem(name="steps", value=int(adjusted_steps), io_type=IOType.STEP),
                GenerationInputItem(name="cfg", value=float(self.config.get("cfg", 5.5)), io_type=IOType.CFG),
                GenerationInputItem(name="sampler", value=self.config.get("sampler", "DPMPP_2M"), io_type=IOType.SAMPLER),
                GenerationInputItem(name="scheduler", value=self.config.get("scheduler", "karras"), io_type=IOType.SCHEDULER),
                GenerationInputItem(name="seed", value=seed if seed else generate_seed(), io_type=IOType.SEED),
                GenerationInputItem(name="clip_skip", value=int(self.config.get("clip_skip", 2)), io_type=IOType.CLIP_SKIP),
                GenerationInputItem(name="image_type", value="pil", io_type=IOType.IMAGE_TYPE),
                GenerationInputItem(name="conditioning", value=conditioning[index] if index < len(conditioning) else conditioning[0], io_type=IOType.CONDITIONING),
            ]),
            generation_outputs=region_generation_output_handler
        ).image

        logger.debug(f"[DETAILER] Enhanced region received: size={enhanced_region.size}, mode={enhanced_region.mode}, extrema={enhanced_region.getextrema()}")

        # Safety check: ensure we got an RGB image, not a mask
        if enhanced_region.mode == 'L':
            logger.error(f"[DETAILER] ERROR: Model returned grayscale image (mask?) instead of RGB! Converting...")
            enhanced_region = enhanced_region.convert('RGB')

        # Downscale if upscaled
        if should_upscale:
            enhanced_region = self.helper.downscale_region(enhanced_region, original_size, "LANCZOS")
            combined_mask = self.helper.downscale_region(combined_mask, original_size, "LANCZOS")

        # Paste enhanced region back
        result_image = self.helper.paste_region(current_image, enhanced_region, combined_mask, coords)

        # Output comparison
        generation_outputs(
            CompareImagesGenerationOutput(
                index=index,
                compare=(None, region_img),
                to=(f"SDXL Enhanced {self.detection_type.title()}", enhanced_region)
            )
        )

        return result_image

    def _get_mask_blend_factor(self) -> float:
        """
        Get the blending factor for combining region mask with adaptive mask.
        Different detection types may use different blend factors.
        """
        blend_factors = {
            "face": 0.3,
            "hand": 0.4,
            "eyes": 0.3,
            "teeth": 0.3
        }
        return blend_factors.get(self.detection_type, 0.3)
