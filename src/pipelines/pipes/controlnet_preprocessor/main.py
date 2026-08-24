from pathlib import Path
from typing import Dict, Any, List, Optional
import numpy as np
from PIL import Image

from src.platform.assets import asset_subdir
from src.pipelines.outputs import ProgressGenerationOutput, ImageGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.outputs import Icon, Progress

_no_preprocessors_warned = False

# Every weight-backed detector below loads from this one repo. `controlnet_aux`
# knows each detector's own filename within it, so handing it the mirror
# directory keeps that knowledge in the library instead of duplicating a table
# of checkpoint filenames here.
ANNOTATORS_REPO = "lllyasviel/Annotators"

# Preprocessor types whose detector loads weights; the rest (canny) are pure
# image ops needing no fetch.
_WEIGHTED_PREPROCESSORS = frozenset(
    {"depth", "openpose", "normal", "scribble", "lineart", "mlsd", "hed"}
)

# Import controlnet_aux processors
try:
    from controlnet_aux import CannyDetector, HEDdetector, MLSDdetector, OpenposeDetector
    from controlnet_aux import LineartDetector, NormalBaeDetector, MidasDetector
    CONTROLNET_AUX_AVAILABLE = True
except ImportError:
    CONTROLNET_AUX_AVAILABLE = False
    logger.warning("[CONTROLNET PREPROCESSOR] controlnet_aux not available - install with: pip install controlnet-aux")


class ControlNetPreprocessorPipe(BasePipe):
    name = "controlnet_preprocessor"
    description = "Preprocess images for ControlNet (Canny, Depth, OpenPose, etc.)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "preprocessors": [],  # List of preprocessor configurations
            "output_resolution": None,  # Optional resize for output (e.g., [512, 512])
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("preprocessors", list, [], "List of preprocessor configurations", required=False),
            PipeConfigSpec("output_resolution", list, None, "Optional output resolution [width, height]", required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """Preprocessor takes input images"""
        return [
            PipeInputSpec("image", IOType.IMAGE, True, "Input images to preprocess", is_array=True),
            PipeInputSpec("ASSETS", IOType.SERVICE, False, "Asset fetcher, to mirror the annotator weights into the depot", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """Preprocessor produces control images"""
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Preprocessed control images", is_array=True),
        ]

    @staticmethod
    def _resolve_annotators(assets, preprocessors_config: List[Dict[str, Any]]) -> Optional[str]:
        """The local annotator-weights directory, or None if none is needed.

        Resolved once, before the per-image loop, and deliberately outside the
        per-detector `except Exception: return image` handlers - a failed fetch
        must fail the generation, not quietly hand the generator an
        unprocessed photo as its control map.
        """
        needed = any(
            config.get("enabled", False)
            and str(config.get("type", "canny")).lower() in _WEIGHTED_PREPROCESSORS
            for config in preprocessors_config
        )
        if not needed:
            return None
        if assets is None:
            raise RuntimeError(
                "ControlNet preprocessing needs the annotator weights but no ASSETS "
                "service is available to mirror them into the model depot."
            )
        return str(
            assets.ensure_asset_repo(
                ANNOTATORS_REPO, subdir=asset_subdir("annotators", ANNOTATORS_REPO)
            )
        )

    def _preprocess_canny(self, image: Image.Image, params: Dict[str, Any]) -> Image.Image:
        """Apply Canny edge detection"""
        try:
            if not CONTROLNET_AUX_AVAILABLE:
                raise ImportError("controlnet_aux not available")

            low_threshold = params.get('low_threshold', 100)
            high_threshold = params.get('high_threshold', 200)

            detector = CannyDetector()
            processed = detector(image, low_threshold=low_threshold, high_threshold=high_threshold)

            logger.debug(f"[CONTROLNET PREPROCESSOR] Canny edge detection applied (thresholds: {low_threshold}, {high_threshold})")
            return processed

        except Exception as e:
            logger.error(f"[CONTROLNET PREPROCESSOR] Canny preprocessing failed: {e}")
            return image

    def _preprocess_depth(self, image: Image.Image, params: Dict[str, Any], annotators: str) -> Image.Image:
        """Apply depth map generation using MiDaS"""
        try:
            if not CONTROLNET_AUX_AVAILABLE:
                raise ImportError("controlnet_aux not available")

            detector = MidasDetector.from_pretrained(annotators)
            processed = detector(image)

            logger.debug("[CONTROLNET PREPROCESSOR] Depth map generated using MiDaS")
            return processed

        except Exception as e:
            logger.error(f"[CONTROLNET PREPROCESSOR] Depth preprocessing failed: {e}")
            return image

    def _preprocess_openpose(self, image: Image.Image, params: Dict[str, Any], annotators: str) -> Image.Image:
        """Apply OpenPose skeleton detection"""
        try:
            if not CONTROLNET_AUX_AVAILABLE:
                raise ImportError("controlnet_aux not available")

            include_body = params.get('include_body', True)
            include_hand = params.get('include_hand', False)
            include_face = params.get('include_face', False)

            detector = OpenposeDetector.from_pretrained(annotators)
            processed = detector(
                image,
                include_body=include_body,
                include_hand=include_hand,
                include_face=include_face
            )

            logger.debug(f"[CONTROLNET PREPROCESSOR] OpenPose applied (body:{include_body}, hand:{include_hand}, face:{include_face})")
            return processed

        except Exception as e:
            logger.error(f"[CONTROLNET PREPROCESSOR] OpenPose preprocessing failed: {e}")
            return image

    def _preprocess_normal(self, image: Image.Image, params: Dict[str, Any], annotators: str) -> Image.Image:
        """Apply normal map generation"""
        try:
            if not CONTROLNET_AUX_AVAILABLE:
                raise ImportError("controlnet_aux not available")

            detector = NormalBaeDetector.from_pretrained(annotators)
            processed = detector(image)

            logger.debug("[CONTROLNET PREPROCESSOR] Normal map generated")
            return processed

        except Exception as e:
            logger.error(f"[CONTROLNET PREPROCESSOR] Normal preprocessing failed: {e}")
            return image

    def _preprocess_scribble(self, image: Image.Image, params: Dict[str, Any], annotators: str) -> Image.Image:
        """Apply scribble/sketch detection using HED"""
        try:
            if not CONTROLNET_AUX_AVAILABLE:
                raise ImportError("controlnet_aux not available")

            detector = HEDdetector.from_pretrained(annotators)
            processed = detector(image, scribble=True)

            logger.debug("[CONTROLNET PREPROCESSOR] Scribble detection applied")
            return processed

        except Exception as e:
            logger.error(f"[CONTROLNET PREPROCESSOR] Scribble preprocessing failed: {e}")
            return image

    def _preprocess_lineart(self, image: Image.Image, params: Dict[str, Any], annotators: str) -> Image.Image:
        """Apply line art detection"""
        try:
            if not CONTROLNET_AUX_AVAILABLE:
                raise ImportError("controlnet_aux not available")

            detector = LineartDetector.from_pretrained(annotators)
            processed = detector(image)

            logger.debug("[CONTROLNET PREPROCESSOR] Line art detection applied")
            return processed

        except Exception as e:
            logger.error(f"[CONTROLNET PREPROCESSOR] Line art preprocessing failed: {e}")
            return image

    def _preprocess_mlsd(self, image: Image.Image, params: Dict[str, Any], annotators: str) -> Image.Image:
        """Apply MLSD line detection"""
        try:
            if not CONTROLNET_AUX_AVAILABLE:
                raise ImportError("controlnet_aux not available")

            detector = MLSDdetector.from_pretrained(annotators)
            processed = detector(image)

            logger.debug("[CONTROLNET PREPROCESSOR] MLSD line detection applied")
            return processed

        except Exception as e:
            logger.error(f"[CONTROLNET PREPROCESSOR] MLSD preprocessing failed: {e}")
            return image

    def _preprocess_hed(self, image: Image.Image, params: Dict[str, Any], annotators: str) -> Image.Image:
        """Apply HED edge detection"""
        try:
            if not CONTROLNET_AUX_AVAILABLE:
                raise ImportError("controlnet_aux not available")

            detector = HEDdetector.from_pretrained(annotators)
            processed = detector(image)

            logger.debug("[CONTROLNET PREPROCESSOR] HED edge detection applied")
            return processed

        except Exception as e:
            logger.error(f"[CONTROLNET PREPROCESSOR] HED preprocessing failed: {e}")
            return image

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        images = pipe_input.input.get("image", [])
        preprocessors_config = self.config.get("preprocessors", [])

        if not images:
            logger.error("[CONTROLNET PREPROCESSOR] No input images provided")
            return PipeOutput(output={"image": []})

        if not isinstance(images, list):
            images = [images]

        if not preprocessors_config:
            global _no_preprocessors_warned
            if not _no_preprocessors_warned:
                logger.warning("[CONTROLNET PREPROCESSOR] No preprocessors configured, passing through images")
                _no_preprocessors_warned = True
            return PipeOutput(output={"image": images})

        # Check if controlnet_aux is available
        if not CONTROLNET_AUX_AVAILABLE:
            logger.error("[CONTROLNET PREPROCESSOR] controlnet_aux not available - skipping preprocessing")
            generation_outputs(ProgressGenerationOutput(
                state="ControlNet preprocessing unavailable - install controlnet-aux",
                icon=Icon("x-circle")
            ))
            return PipeOutput(output={"image": images})

        annotators = self._resolve_annotators(
            pipe_input.input.get("ASSETS"), preprocessors_config
        )

        generation_outputs(ProgressGenerationOutput(
            state=f"Preprocessing <<NUMBER:{len(images)} images:image>> for ControlNet",
            icon=Icon("image"),
            progress=Progress(0, 100)
        ))

        processed_images = []

        for i, (image, preprocessor_config) in enumerate(zip(images, preprocessors_config)):
            if not preprocessor_config.get('enabled', False):
                logger.debug(f"[CONTROLNET PREPROCESSOR] Preprocessor {i+1} disabled, using original image")
                processed_images.append(image)
                continue

            preprocessor_type = preprocessor_config.get('type', 'canny').lower()
            params = preprocessor_config.get('parameters', {})

            generation_outputs(ProgressGenerationOutput(
                state=f"Applying <<EFFECT:{preprocessor_type}:wand>> to image {i+1}/{len(images)}",
                icon=Icon("wand"),
                progress=Progress((i * 80) // len(images), 100)
            ))

            # Apply the appropriate preprocessor
            if preprocessor_type == 'canny':
                processed = self._preprocess_canny(image, params)
            elif preprocessor_type == 'depth':
                processed = self._preprocess_depth(image, params, annotators)
            elif preprocessor_type == 'openpose':
                processed = self._preprocess_openpose(image, params, annotators)
            elif preprocessor_type == 'normal':
                processed = self._preprocess_normal(image, params, annotators)
            elif preprocessor_type == 'scribble':
                processed = self._preprocess_scribble(image, params, annotators)
            elif preprocessor_type == 'lineart':
                processed = self._preprocess_lineart(image, params, annotators)
            elif preprocessor_type == 'mlsd':
                processed = self._preprocess_mlsd(image, params, annotators)
            elif preprocessor_type == 'hed':
                processed = self._preprocess_hed(image, params, annotators)
            else:
                logger.warning(f"[CONTROLNET PREPROCESSOR] Unknown preprocessor type: {preprocessor_type}")
                processed = image

            # Optional: resize to output resolution
            output_resolution = self.config.get("output_resolution")
            if output_resolution and len(output_resolution) == 2:
                processed = processed.resize((output_resolution[0], output_resolution[1]), Image.LANCZOS)
                logger.debug(f"[CONTROLNET PREPROCESSOR] Resized to {output_resolution[0]}x{output_resolution[1]}")

            # Output preview of processed image
            generation_outputs(ImageGenerationOutput(
                image=processed,
                temporary=True,
                isArtifact=True,
                label="Preprocessed Control Image"
            ))

            processed_images.append(processed)


        generation_outputs(ProgressGenerationOutput(
            state=f"Preprocessed <<NUMBER:{len(processed_images)} control images:check-circle>>",
            icon=Icon("check-circle"),
            progress=Progress(100, 100)
        ))

        logger.debug(f"[CONTROLNET PREPROCESSOR] Successfully preprocessed {len(processed_images)} images")

        return PipeOutput(output={"image": processed_images})
