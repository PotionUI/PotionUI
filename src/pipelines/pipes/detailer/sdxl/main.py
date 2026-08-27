from typing import Dict, Any, List
import torch

from src.pipelines.models import Model
from src.pipelines.contracts import BasePipe
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.pipes._shared.detection.detailer_helper import DetailerHelper
from src.pipelines.pipes.detailer.sdxl.detection_processor import BaseDetectionProcessor
from src.pipelines.pipes._shared.detection import FaceDetector, HandDetector, EyeDetector, TeethDetector, PersonDetector


class ADetailerSDXLPipe(BasePipe):
    name = "detailer"
    description = "SDXL-optimized automatic detection and enhancement of faces and hands"

    def __init__(self, config=None):
        super().__init__(config)
        self.helper = DetailerHelper(self.config)

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("conditioning", IOType.CONDITIONING, True, "Encoded prompt conditioning", is_array=True),
            PipeInputSpec("model", IOType.MODEL, True, "AI model for image generation", is_array=False),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds for generation", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, True, "Input images to enhance", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Enhanced images with detailed faces and hands", is_array=True),
        ]

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        # SDXL-optimized defaults
        std_config = {
            "confidence": 0.35,
            "mask_min_ratio": 0.008,
            "mask_max_ratio": 0.6,
            "strength": 0.12,
            "cfg": 5.5,
            "steps": 25,
            "padding": 32,
            "mask_blur": 0.5,
            "device": "cuda" if torch.cuda.is_available() else "cpu",
            "box_color": [0, 0, 0],
            "box_thickness": 8,
            "size_threshold": 0.08,
            "step_reduction_percentage": 0.6,
            "enable_upscale": True,
            "upscale_factor": 1.5,
            "upscale_method": "LANCZOS",
            "min_size_for_upscale": 96,
            "sampler": "DPMPP_2M",
            "scheduler": "karras",
        }

        # SDXL-specific detection configuration
        config = {
            "detect": ["face", "hand"],
            "detections": {
                "face": {
                    "type": "yolo",  # Detection backend: yolo or mediapipe
                    "model": "models/detection_bbox/face_yolov12m.pt",
                },
                "hand": {
                    "type": "yolo",  # Detection backend: yolo or mediapipe
                    "model": "models/detection_bbox/hand_yolov8n.pt",
                    "confidence": 0.5,
                    "mask_min_ratio": 0.005,
                    "mask_max_ratio": 0.6,
                    "strength": 0.3,
                    "cfg": 7,
                    "steps": 20,
                    "padding": 32,
                    "mask_blur": 2,
                    "box_color": [0, 255, 0],
                },
                "eyes": {
                    "enabled": False,
                    "type": "mediapipe",  # Eyes always use MediaPipe
                    "model": "models/mediapipe/face_landmarker.task",
                    "confidence": 0.5,
                    "strength": 0.15,
                    "cfg": 4.5,
                    "steps": 20,
                    "eye_padding": 10,
                    "eye_v_padding": 15,
                    "mask_blur": 1,
                    "box_color": [255, 255, 0],
                    "box_thickness": 4,
                    "sampler": "DPMPP_2M",
                    "scheduler": "karras",
                    "padding": 20,
                    "clip_skip": 2,
                },
                "teeth": {
                    "enabled": False,
                    "type": "mediapipe",  # Teeth always use MediaPipe
                    "model": "models/mediapipe/face_landmarker.task",
                    "confidence": 0.5,
                    "strength": 0.12,
                    "cfg": 4.5,
                    "steps": 20,
                    "mouth_open_threshold": 0.015,
                    "teeth_padding": 5,
                    "teeth_v_padding": 8,
                    "mask_blur": 0,
                    "box_color": [255, 255, 255],
                    "box_thickness": 4,
                    "sampler": "DPMPP_2M",
                    "scheduler": "karras",
                    "padding": 15,
                    "clip_skip": 2,
                },
                "person": {
                    "enabled": False,
                    "type": "yolo",  # Person uses YOLO detection with COCO model
                    "model": "yolov8m.pt",  # Standard YOLOv8 COCO model (class 0 = person)
                    "confidence": 0.4,
                    "mask_min_ratio": 0.01,
                    "mask_max_ratio": 0.9,
                    "strength": 0.25,
                    "cfg": 5.0,
                    "steps": 20,
                    "padding": 48,
                    "mask_blur": 3,
                    "box_color": [255, 0, 255],
                    "box_thickness": 8,
                    "sampler": "DPMPP_2M",
                    "scheduler": "karras",
                    "clip_skip": 2,
                }
            }
        }

        # Merge SDXL-optimized defaults
        config["detections"]["face"].update(std_config)
        # Merge std_config with hand but keep hand-specific overrides
        hand_config = config["detections"]["hand"].copy()
        config["detections"]["hand"].update(std_config)
        config["detections"]["hand"].update(hand_config)
        # Merge std_config with eyes but keep eyes-specific overrides
        eyes_config = config["detections"]["eyes"].copy()
        config["detections"]["eyes"].update(std_config)
        config["detections"]["eyes"].update(eyes_config)
        # Merge std_config with teeth but keep teeth-specific overrides
        teeth_config = config["detections"]["teeth"].copy()
        config["detections"]["teeth"].update(std_config)
        config["detections"]["teeth"].update(teeth_config)
        # Merge std_config with person but keep person-specific overrides
        person_config = config["detections"]["person"].copy()
        config["detections"]["person"].update(std_config)
        config["detections"]["person"].update(person_config)

        return config

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """SDXL-specific configuration parameters"""
        return [
            PipeConfigSpec("detect", list, ["face", "hand"], "Types of objects to detect", required=False,
                          choices=["face", "hand", "eyes", "teeth", "person"]),
            PipeConfigSpec("detections", dict, {}, "Detection configuration for each object type", required=False),

            # Common detection parameters
            PipeConfigSpec("confidence", float, 0.35, "Detection confidence threshold", required=False,
                          min_value=0.0, max_value=1.0),
            PipeConfigSpec("mask_min_ratio", float, 0.008, "Minimum mask size ratio", required=False,
                          min_value=0.001, max_value=1.0),
            PipeConfigSpec("mask_max_ratio", float, 0.6, "Maximum mask size ratio", required=False,
                          min_value=0.1, max_value=1.0),
            PipeConfigSpec("strength", float, 0.12, "Denoising strength (SDXL optimized)", required=False,
                          min_value=0.0, max_value=0.5),
            PipeConfigSpec("cfg", float, 5.5, "CFG scale (SDXL optimized)", required=False,
                          min_value=1.0, max_value=15.0),
            PipeConfigSpec("steps", int, 25, "Number of inference steps (SDXL optimized)", required=False,
                          min_value=10, max_value=50),
            PipeConfigSpec("padding", int, 32, "Padding around detected regions", required=False,
                          min_value=0, max_value=200),
            PipeConfigSpec("mask_blur", int, 0, "Mask blur radius (SDXL optimized)", required=False,
                          min_value=0, max_value=50),
            PipeConfigSpec("device", str, "cuda", "Device to use for processing", required=False,
                          choices=["cuda", "cpu", "mps"]),
            PipeConfigSpec("box_color", list, [0, 0, 0], "Detection box color (RGB)", required=False),
            PipeConfigSpec("box_thickness", int, 8, "Detection box thickness", required=False,
                          min_value=1, max_value=50),
            PipeConfigSpec("size_threshold", float, 0.08, "Minimum detection size threshold", required=False,
                          min_value=0.01, max_value=1.0),
            PipeConfigSpec("step_reduction_percentage", float, 0.6, "Step reduction for large images", required=False,
                          min_value=0.1, max_value=1.0),
            PipeConfigSpec("enable_upscale", bool, True, "Enable upscaling before detailing", required=False),
            PipeConfigSpec("upscale_factor", float, 1.5, "Conservative upscaling factor for SDXL", required=False,
                          min_value=1.0, max_value=2.5),
            PipeConfigSpec("upscale_method", str, "LANCZOS", "Upscaling interpolation method", required=False,
                          choices=["LANCZOS", "NEAREST", "BILINEAR", "BICUBIC"]),
            PipeConfigSpec("min_size_for_upscale", int, 96, "Minimum face size to apply upscaling", required=False,
                          min_value=32, max_value=512),
            PipeConfigSpec(
                name="sampler",
                param_type=str,
                default="DPMPP_2M",
                description="Sampling algorithm to use",
                required=False,
                choices=["EULER", "EULER_A", "HEUN", "DPM2", "DPM2_A", "LMS",
                         "DPMPP_2S_A", "DPMPP_SDE", "DPMPP_2M", "DPMPP_2M_SDE", "DPMPP_3M_SDE", "LCM"]
            ),
            PipeConfigSpec(
                name="scheduler",
                param_type=str,
                default="karras",
                description="Noise schedule type to use",
                required=False,
                choices=["normal", "karras", "exponential", "sgm_uniform", "simple", "ddim_uniform"]
            )
        ]

    def _cast_config_types(self):
        """Cast all configuration values to proper types to avoid string multiplication errors"""
        def cast_value(value, target_type):
            if value is None:
                return None
            try:
                if target_type == int:
                    return int(float(value))
                elif target_type == float:
                    return float(value)
                elif target_type == bool:
                    return str(value).lower() in ('true', '1', 'yes')
                else:
                    return value
            except (ValueError, TypeError):
                return value

        # Cast detection-specific values for each type
        for detection_type in ['face', 'hand', 'eyes', 'teeth', 'person']:
            if detection_type in self.config.get('detections', {}):
                det_config = self.config['detections'][detection_type]

                numeric_float_fields = ['confidence', 'strength', 'cfg', 'mask_blur', 'upscale_factor']
                numeric_int_fields = ['steps', 'padding', 'min_size_for_upscale']

                for field in numeric_float_fields:
                    if field in det_config:
                        det_config[field] = cast_value(det_config[field], float)

                for field in numeric_int_fields:
                    if field in det_config:
                        det_config[field] = cast_value(det_config[field], int)

                # Type-specific fields
                if detection_type == 'eyes':
                    if 'eye_padding' in det_config:
                        det_config['eye_padding'] = cast_value(det_config['eye_padding'], int)
                    if 'eye_v_padding' in det_config:
                        det_config['eye_v_padding'] = cast_value(det_config['eye_v_padding'], int)

                if detection_type == 'teeth':
                    if 'mouth_open_threshold' in det_config:
                        det_config['mouth_open_threshold'] = cast_value(det_config['mouth_open_threshold'], float)
                    if 'teeth_padding' in det_config:
                        det_config['teeth_padding'] = cast_value(det_config['teeth_padding'], int)
                    if 'teeth_v_padding' in det_config:
                        det_config['teeth_v_padding'] = cast_value(det_config['teeth_v_padding'], int)

    def _create_detector(self, detection_type: str):
        """
        Factory method to create the appropriate detector based on type.
        """
        detector_classes = {
            "face": FaceDetector,
            "hand": HandDetector,
            "eyes": EyeDetector,
            "teeth": TeethDetector,
            "person": PersonDetector
        }

        detector_class = detector_classes.get(detection_type)
        if not detector_class:
            raise ValueError(f"Unknown detection type: {detection_type}")

        # Get detector-specific config from detections dict
        detector_config = self.config["detections"].get(detection_type, {})

        return detector_class(detector_config, self.helper)

    def _is_detection_enabled(self, detection_type: str) -> bool:
        """
        Check if a detection type is enabled.
        """
        # Check if in detect list
        if detection_type not in self.config.get("detect", []):
            return False

        # Check if explicitly disabled in detections config
        det_config = self.config.get("detections", {}).get(detection_type, {})
        enabled = det_config.get("enabled", "true")

        # Handle both boolean and string values
        if isinstance(enabled, bool):
            return enabled
        return str(enabled).lower() == "true"

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable
    ) -> PipeOutput:
        """
        SDXL-optimized processing flow using modular detector architecture.

        Flow:
        1. Cast configuration types
        2. Extract inputs
        3. For each enabled detection type:
           - Create detector
           - Process all images with detector
        4. Return enhanced images
        """
        # Cast all config values to proper types
        self._cast_config_types()

        # Extract inputs
        conditioning = pipe_input.input.get("conditioning", [])
        model: Model = pipe_input.input["model"]
        seeds = pipe_input.input.get("seed", self.config.get("seed", []))
        input_images = pipe_input.input["image"]

        if not isinstance(conditioning, list):
            conditioning = [conditioning]
        if not isinstance(input_images, list):
            input_images = [input_images]

        # Process each detection type
        # Person runs FIRST to enhance full body before face/hand/eyes/teeth details
        detection_types = ["person", "face", "hand", "eyes", "teeth"]

        results = []
        for index, image in enumerate(input_images):
            current_image = image.convert("RGB")
            seed = seeds[index] if seeds and index < len(seeds) else (seeds[0] if seeds else None)

            # Apply each enabled detector sequentially
            for detection_type in detection_types:
                if not self._is_detection_enabled(detection_type):
                    continue

                # Create detector and processor
                detector = self._create_detector(detection_type)
                processor = BaseDetectionProcessor(detector, self.helper)

                # Process image
                current_image = processor.process_detection(
                    image=current_image,
                    model=model,
                    conditioning=conditioning,
                    seed=seed,
                    index=index,
                    generation_outputs=generation_outputs
                )

            results.append(current_image)

        # One aggressive cleanup per pipe run; per-region img2img cleanups are
        # light (no sync/multi-GC) by design.
        from src.platform.runtime.model_lifecycle.lifecycle import get_model_lifecycle
        models = get_model_lifecycle()
        if models is not None:
            models.cleanup(aggressive=True)
        elif hasattr(model, "clear_cuda_cache"):
            model.clear_cuda_cache(aggressive=True)

        return PipeOutput({"image": results})
