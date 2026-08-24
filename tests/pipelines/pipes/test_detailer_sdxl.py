import unittest
from unittest.mock import Mock, MagicMock, patch
from PIL import Image
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="cv2 required by ultralytics (detailer sdxl dependency)", exc_type=ImportError)  # noqa: F841

from src.pipelines.pipes.detailer.sdxl.main import ADetailerSDXLPipe
from src.pipelines.pipes._shared.detection import FaceDetector, HandDetector, EyeDetector, TeethDetector, PersonDetector
from src.pipelines.pipes.detailer.sdxl.detection_processor import BaseDetectionProcessor
from src.pipelines.contracts import PipeInput, IOType


class TestADetailerSDXLPipe(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            "detect": ["face", "hand"],
            "detections": {
                "face": {
                    "type": "yolo",
                    "model": "models/adetailer/face_yolov12m.pt",
                    "enabled": "true",
                    "confidence": 0.35,
                    "strength": 0.12,
                    "cfg": 5.5,
                    "steps": 25,
                    "padding": 32,
                    "mask_blur": 0.5,
                    "box_color": (0, 0, 0),
                    "box_thickness": 8,
                    "sampler": "DPMPP_2M",
                    "scheduler": "karras",
                    "enable_upscale": True,
                    "upscale_factor": 1.5,
                    "min_size_for_upscale": 96,
                    "mask_min_ratio": 0.008,
                    "mask_max_ratio": 0.6,
                    "size_threshold": 0.08,
                    "step_reduction_percentage": 0.6,
                },
                "hand": {
                    "type": "yolo",
                    "model": "models/adetailer/hand_yolov8n.pt",
                    "enabled": "true",
                    "confidence": 0.5,
                    "strength": 0.3,
                    "cfg": 7,
                    "steps": 20,
                    "padding": 32,
                    "mask_blur": 2,
                    "box_color": (0, 255, 0),
                    "box_thickness": 8,
                    "sampler": "DPMPP_2M",
                    "scheduler": "karras",
                    "mask_min_ratio": 0.005,
                    "mask_max_ratio": 0.6,
                },
                "eyes": {
                    "type": "mediapipe",
                    "enabled": "false",
                },
                "teeth": {
                    "type": "mediapipe",
                    "enabled": "false",
                },
                "person": {
                    "type": "yolo",
                    "enabled": "false",
                }
            },
            "device": "cpu",
        }

        self.pipe = ADetailerSDXLPipe(self.config)

    def test_inputs_outputs(self):
        """Test input and output specifications"""
        inputs = ADetailerSDXLPipe.inputs()
        outputs = ADetailerSDXLPipe.outputs()

        # Check inputs
        input_names = [i.name for i in inputs]
        self.assertIn("conditioning", input_names)
        self.assertIn("model", input_names)
        self.assertIn("seed", input_names)
        self.assertIn("image", input_names)

        # Check outputs
        output_names = [o.name for o in outputs]
        self.assertIn("image", output_names)

        # Check that image output is array
        image_output = next(o for o in outputs if o.name == "image")
        self.assertTrue(image_output.is_array)

    def test_configuration(self):
        """Test configuration specifications"""
        config_specs = ADetailerSDXLPipe.configuration()

        # Check that required configuration parameters are defined
        param_names = [spec.name for spec in config_specs]
        self.assertIn("detect", param_names)
        self.assertIn("detections", param_names)
        self.assertIn("confidence", param_names)
        self.assertIn("strength", param_names)
        self.assertIn("cfg", param_names)
        self.assertIn("steps", param_names)
        self.assertIn("sampler", param_names)
        self.assertIn("scheduler", param_names)

        # Verify detections is a dict type
        detections_spec = next(s for s in config_specs if s.name == "detections")
        self.assertEqual(detections_spec.param_type, dict)

    def test_get_default_config(self):
        """Test default configuration"""
        config = ADetailerSDXLPipe.get_default_config()

        # Check main structure
        self.assertIn("detect", config)
        self.assertIn("detections", config)

        # Check detection types
        self.assertIn("face", config["detections"])
        self.assertIn("hand", config["detections"])
        self.assertIn("eyes", config["detections"])
        self.assertIn("teeth", config["detections"])
        self.assertIn("person", config["detections"])

        # Check face config has required fields
        face_config = config["detections"]["face"]
        self.assertIn("model", face_config)
        self.assertIn("confidence", face_config)
        self.assertIn("strength", face_config)
        self.assertIn("cfg", face_config)
        self.assertIn("steps", face_config)

        # Check SDXL-optimized defaults
        self.assertEqual(face_config["confidence"], 0.35)
        self.assertEqual(face_config["strength"], 0.12)
        self.assertEqual(face_config["cfg"], 5.5)
        self.assertEqual(face_config["steps"], 25)

    def test_cast_config_types(self):
        """Test configuration type casting"""
        # Test with string values that need casting
        self.pipe.config["detections"]["face"]["strength"] = "0.5"
        self.pipe.config["detections"]["face"]["steps"] = "30"
        self.pipe.config["detections"]["eyes"]["eye_padding"] = "15"
        self.pipe.config["detections"]["teeth"]["mouth_open_threshold"] = "0.02"

        self.pipe._cast_config_types()

        # Check that values are properly cast
        self.assertIsInstance(self.pipe.config["detections"]["face"]["strength"], float)
        self.assertEqual(self.pipe.config["detections"]["face"]["strength"], 0.5)

        self.assertIsInstance(self.pipe.config["detections"]["face"]["steps"], int)
        self.assertEqual(self.pipe.config["detections"]["face"]["steps"], 30)

        self.assertIsInstance(self.pipe.config["detections"]["eyes"]["eye_padding"], int)
        self.assertEqual(self.pipe.config["detections"]["eyes"]["eye_padding"], 15)

        self.assertIsInstance(self.pipe.config["detections"]["teeth"]["mouth_open_threshold"], float)
        self.assertEqual(self.pipe.config["detections"]["teeth"]["mouth_open_threshold"], 0.02)

    def test_create_detector_face(self):
        """Test creating face detector"""
        detector = self.pipe._create_detector("face")

        self.assertIsInstance(detector, FaceDetector)
        self.assertEqual(detector.get_detection_type(), "face")
        self.assertEqual(detector.config.get("model"), "models/adetailer/face_yolov12m.pt")

    def test_create_detector_hand(self):
        """Test creating hand detector"""
        detector = self.pipe._create_detector("hand")

        self.assertIsInstance(detector, HandDetector)
        self.assertEqual(detector.get_detection_type(), "hand")
        self.assertEqual(detector.config.get("model"), "models/adetailer/hand_yolov8n.pt")

    def test_create_detector_eyes(self):
        """Test creating eye detector"""
        detector = self.pipe._create_detector("eyes")

        self.assertIsInstance(detector, EyeDetector)
        self.assertEqual(detector.get_detection_type(), "eyes")

    def test_create_detector_teeth(self):
        """Test creating teeth detector"""
        detector = self.pipe._create_detector("teeth")

        self.assertIsInstance(detector, TeethDetector)
        self.assertEqual(detector.get_detection_type(), "teeth")

    def test_create_detector_person(self):
        """Test creating person detector"""
        detector = self.pipe._create_detector("person")

        self.assertIsInstance(detector, PersonDetector)
        self.assertEqual(detector.get_detection_type(), "person")

    def test_create_detector_with_custom_model(self):
        """Test creating detector with custom model path in detections config"""
        # Set custom model path in detections config
        self.pipe.config["detections"]["face"]["model"] = "models/adetailer/custom_face_model.pt"

        detector = self.pipe._create_detector("face")

        # Should use custom model path from detections config
        self.assertEqual(detector.config.get("model"), "models/adetailer/custom_face_model.pt")

    def test_create_detector_invalid_type(self):
        """Test creating detector with invalid type"""
        with self.assertRaises(ValueError):
            self.pipe._create_detector("invalid_type")

    def test_is_detection_enabled_true(self):
        """Test detection enablement check - enabled"""
        # Face is in detect list and enabled
        self.assertTrue(self.pipe._is_detection_enabled("face"))

    def test_is_detection_enabled_not_in_list(self):
        """Test detection enablement check - not in detect list"""
        # Eyes is not in detect list
        self.assertFalse(self.pipe._is_detection_enabled("eyes"))

    def test_is_detection_enabled_explicitly_disabled(self):
        """Test detection enablement check - explicitly disabled"""
        # Add eyes to detect list but keep it disabled
        self.pipe.config["detect"].append("eyes")

        self.assertFalse(self.pipe._is_detection_enabled("eyes"))

    def test_is_detection_enabled_boolean(self):
        """Test detection enablement check with boolean value"""
        # Set enabled as boolean instead of string
        self.pipe.config["detections"]["face"]["enabled"] = False

        self.assertFalse(self.pipe._is_detection_enabled("face"))

        self.pipe.config["detections"]["face"]["enabled"] = True

        self.assertTrue(self.pipe._is_detection_enabled("face"))

    def test_is_detection_enabled_default_true(self):
        """Test detection enablement check with missing enabled field"""
        # Remove enabled field (should default to true)
        if "enabled" in self.pipe.config["detections"]["face"]:
            del self.pipe.config["detections"]["face"]["enabled"]

        self.assertTrue(self.pipe._is_detection_enabled("face"))

    def test_default_config_keeps_hand_overrides(self):
        """The per-type blocks in get_default_config() are merged with the shared
        std_config. hand's own values were merged the wrong way round, so every
        key it declared - box_color first among them - was overwritten by the
        shared default before anything read it."""
        defaults = ADetailerSDXLPipe.get_default_config()
        hand = defaults["detections"]["hand"]

        self.assertEqual(hand["box_color"], [0, 255, 0])
        self.assertEqual(hand["strength"], 0.3)
        self.assertEqual(hand["cfg"], 7)
        self.assertEqual(hand["steps"], 20)
        self.assertEqual(hand["mask_blur"], 2)
        self.assertEqual(hand["confidence"], 0.5)

        # The shared defaults hand does not override still reach it.
        self.assertEqual(hand["sampler"], "DPMPP_2M")
        self.assertEqual(hand["scheduler"], "karras")

        # face declares no std_config key, so it takes the shared values whole.
        self.assertEqual(defaults["detections"]["face"]["box_color"], [0, 0, 0])
        self.assertEqual(defaults["detections"]["face"]["strength"], 0.12)

    def test_default_detector_paths_point_at_the_real_models_dir(self):
        """`models/detection_bbox` is the directory the model indexer and the
        downloader use; `models/detailer_bbox` never existed."""
        defaults = ADetailerSDXLPipe.get_default_config()

        self.assertEqual(
            defaults["detections"]["face"]["model"],
            "models/detection_bbox/face_yolov12m.pt",
        )
        self.assertEqual(
            defaults["detections"]["hand"]["model"],
            "models/detection_bbox/hand_yolov8n.pt",
        )

    @patch('src.pipelines.pipes.detailer.sdxl.detection_processor.BaseDetectionProcessor.process_detection')
    @patch('src.pipelines.pipes._shared.detection.face_detector.FaceDetector')
    def test_process(self, mock_face_detector_class, mock_process_detection):
        """Test process method integration"""
        # Create test data
        test_image = Image.new('RGB', (512, 512), color='red')
        mock_model = Mock()
        mock_conditioning = [Mock()]

        # Setup pipe input
        pipe_input = PipeInput(input={
            "image": [test_image],
            "model": mock_model,
            "conditioning": mock_conditioning,
            "seed": [12345]
        })

        # Mock process_detection to return modified image
        processed_image = Image.new('RGB', (512, 512), color='blue')
        mock_process_detection.return_value = processed_image

        # Mock detector instance
        mock_detector_instance = Mock()
        mock_face_detector_class.return_value = mock_detector_instance

        # Mock generation outputs
        generation_outputs = Mock()

        # Process
        result = self.pipe.process(pipe_input, generation_outputs)

        # Check result
        self.assertIn("image", result.output)
        self.assertEqual(len(result.output["image"]), 1)

        # Verify process_detection was called
        # Note: It should be called twice (face and hand)
        # But we only enabled face in detect list, so once for face
        self.assertTrue(mock_process_detection.called)

    @patch('src.pipelines.pipes._shared.detection.detailer_helper.DetailerHelper.detect_objects')
    @patch('src.pipelines.pipes._shared.detection.detailer_helper.DetailerHelper.load_detector')
    def test_face_detector_integration(self, mock_load_detector, mock_detect_objects):
        """Test face detector integration"""
        # Create face detector
        face_config = self.config["detections"]["face"]
        detector = FaceDetector(face_config, self.pipe.helper)

        # Mock detector model
        mock_detector_model = Mock()
        mock_load_detector.return_value = mock_detector_model

        # Mock detection results
        mock_boxes = [np.array([100, 100, 200, 200])]
        mock_detect_objects.return_value = mock_boxes

        # Test detection
        test_image = Image.new('RGB', (512, 512))
        boxes = detector.detect(test_image)

        # Verify detector was loaded
        mock_load_detector.assert_called_once_with("models/adetailer/face_yolov12m.pt")

        # Verify detect_objects was called
        mock_detect_objects.assert_called_once()

        # Check boxes
        self.assertEqual(len(boxes), 1)

    def test_detection_processor_creation(self):
        """Test detection processor creation"""
        detector = self.pipe._create_detector("face")
        processor = BaseDetectionProcessor(detector, self.pipe.helper)

        self.assertIsNotNone(processor)
        self.assertEqual(processor.detection_type, "face")
        self.assertEqual(processor.detector, detector)

    def test_multiple_detection_types_enabled(self):
        """Test processing with multiple detection types enabled"""
        # Enable both face and hand
        self.pipe.config["detect"] = ["face", "hand"]
        self.pipe.config["detections"]["face"]["enabled"] = "true"
        self.pipe.config["detections"]["hand"]["enabled"] = "true"

        # Check both are enabled
        self.assertTrue(self.pipe._is_detection_enabled("face"))
        self.assertTrue(self.pipe._is_detection_enabled("hand"))

    def test_no_detection_types_enabled(self):
        """Test with no detection types enabled"""
        # Empty detect list
        self.pipe.config["detect"] = []

        # Check none are enabled
        self.assertFalse(self.pipe._is_detection_enabled("face"))
        self.assertFalse(self.pipe._is_detection_enabled("hand"))
        self.assertFalse(self.pipe._is_detection_enabled("eyes"))
        self.assertFalse(self.pipe._is_detection_enabled("teeth"))
        self.assertFalse(self.pipe._is_detection_enabled("person"))


class TestFaceDetector(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            "type": "yolo",
            "model": "models/adetailer/face_yolov12m.pt",
            "confidence": 0.35,
            "steps": 25,
            "size_threshold": 0.08,
            "step_reduction_percentage": 0.6,
        }
        self.mock_helper = Mock()
        self.detector = FaceDetector(self.config, self.mock_helper)

    def test_get_detection_type(self):
        """Test detection type identifier"""
        self.assertEqual(self.detector.get_detection_type(), "face")

    def test_create_mask(self):
        """Test mask creation"""
        self.mock_helper.create_adaptive_mask.return_value = Mock()

        region_size = (256, 256)
        box = [0, 0, 256, 256]
        region_img = Image.new('RGB', region_size)

        mask = self.detector.create_mask(region_size, box, region_img)

        # Verify create_adaptive_mask was called with correct parameters
        self.mock_helper.create_adaptive_mask.assert_called_once_with(
            region_size, box, feather_ratio=0.1
        )

    def test_adjust_steps(self):
        """Test adaptive step adjustment"""
        # Mock helper method
        self.mock_helper.adjust_steps_based_on_face_size.return_value = 30

        box = np.array([100, 100, 300, 300])
        image_size = (512, 512)

        steps = self.detector.adjust_steps(box, image_size)

        # Should cap at 20 for SDXL efficiency
        self.assertEqual(steps, 20)

    def test_adjust_steps_small_face(self):
        """Test step adjustment for small face"""
        # Mock helper to return fewer steps
        self.mock_helper.adjust_steps_based_on_face_size.return_value = 15

        box = np.array([100, 100, 150, 150])
        image_size = (512, 512)

        steps = self.detector.adjust_steps(box, image_size)

        # Should use the returned value since it's below the cap
        self.assertEqual(steps, 15)


class TestHandDetector(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            "type": "yolo",
            "model": "models/adetailer/hand_yolov8n.pt",
            "confidence": 0.5,
            "steps": 20,
        }
        self.mock_helper = Mock()
        self.detector = HandDetector(self.config, self.mock_helper)

    def test_get_detection_type(self):
        """Test detection type identifier"""
        self.assertEqual(self.detector.get_detection_type(), "hand")

    def test_create_mask(self):
        """Test mask creation with hand-specific parameters"""
        self.mock_helper.create_adaptive_mask.return_value = Mock()

        region_size = (256, 256)
        box = [0, 0, 256, 256]
        region_img = Image.new('RGB', region_size)

        mask = self.detector.create_mask(region_size, box, region_img)

        # Verify create_adaptive_mask was called with hand-specific feather ratio
        self.mock_helper.create_adaptive_mask.assert_called_once_with(
            region_size, box, feather_ratio=0.12
        )

    def test_adjust_steps(self):
        """Test fixed step count for hands"""
        box = np.array([100, 100, 200, 200])
        image_size = (512, 512)

        steps = self.detector.adjust_steps(box, image_size)

        # Should use fixed steps from config
        self.assertEqual(steps, 20)


class TestEyeDetector(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            "type": "mediapipe",
            "confidence": 0.5,
            "steps": 20,
        }
        self.mock_helper = Mock()
        self.detector = EyeDetector(self.config, self.mock_helper)

    def test_get_detection_type(self):
        """Test detection type identifier"""
        self.assertEqual(self.detector.get_detection_type(), "eyes")

    def test_detect(self):
        """Test eye detection using MediaPipe"""
        mock_boxes = [np.array([100, 150, 200, 180])]
        self.mock_helper.detect_eyes_mediapipe.return_value = mock_boxes

        test_image = Image.new('RGB', (512, 512))
        boxes = self.detector.detect(test_image)

        # Verify MediaPipe detection was called
        self.mock_helper.detect_eyes_mediapipe.assert_called_once_with(test_image, 'models/mediapipe/face_landmarker.task')
        self.assertEqual(boxes, mock_boxes)

    def test_create_mask(self):
        """Test specialized eye region mask"""
        self.mock_helper.create_eye_region_mask.return_value = Mock()

        region_size = (256, 256)
        box = [0, 0, 256, 256]
        region_img = Image.new('RGB', region_size)

        mask = self.detector.create_mask(region_size, box, region_img)

        # Verify create_eye_region_mask was called
        self.mock_helper.create_eye_region_mask.assert_called_once_with(region_size, box)


class TestTeethDetector(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            "type": "mediapipe",
            "confidence": 0.5,
            "steps": 20,
            "mouth_open_threshold": 0.015,
        }
        self.mock_helper = Mock()
        self.detector = TeethDetector(self.config, self.mock_helper)

    def test_get_detection_type(self):
        """Test detection type identifier"""
        self.assertEqual(self.detector.get_detection_type(), "teeth")

    def test_detect(self):
        """Test teeth detection using MediaPipe"""
        mock_boxes = [np.array([150, 200, 250, 230])]
        self.mock_helper.detect_teeth_mediapipe.return_value = mock_boxes

        test_image = Image.new('RGB', (512, 512))
        boxes = self.detector.detect(test_image)

        # Verify MediaPipe detection was called
        self.mock_helper.detect_teeth_mediapipe.assert_called_once_with(test_image, 'models/mediapipe/face_landmarker.task')
        self.assertEqual(boxes, mock_boxes)

    def test_create_mask(self):
        """Test teeth region mask"""
        self.mock_helper.create_adaptive_mask.return_value = Mock()

        region_size = (256, 256)
        box = [0, 0, 256, 256]
        region_img = Image.new('RGB', region_size)

        mask = self.detector.create_mask(region_size, box, region_img)

        # Verify create_adaptive_mask was called with teeth-specific parameters
        self.mock_helper.create_adaptive_mask.assert_called_once_with(
            region_size, box, feather_ratio=0.1
        )


class TestPersonDetector(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures"""
        self.config = {
            "type": "yolo",
            "model": "yolov8m.pt",  # Standard YOLOv8 COCO model
            "confidence": 0.4,
            "steps": 20,
        }
        self.mock_helper = Mock()
        self.detector = PersonDetector(self.config, self.mock_helper)

    def test_get_detection_type(self):
        """Test detection type identifier"""
        self.assertEqual(self.detector.get_detection_type(), "person")

    def test_detect(self):
        """Test person detection using YOLO with class filtering"""
        mock_detector_model = Mock()
        self.mock_helper.load_detector.return_value = mock_detector_model

        mock_boxes = [np.array([50, 50, 400, 500])]
        self.mock_helper.detect_objects.return_value = mock_boxes

        test_image = Image.new('RGB', (512, 512))
        boxes = self.detector.detect(test_image)

        # Verify detector was loaded with standard YOLOv8 model
        self.mock_helper.load_detector.assert_called_once_with("yolov8m.pt")

        # Verify detect_objects was called with class filtering for person (class 0)
        self.mock_helper.detect_objects.assert_called_once_with(test_image, mock_detector_model, "person", classes=[0])
        self.assertEqual(boxes, mock_boxes)

    def test_create_mask(self):
        """Test mask creation with person-specific parameters"""
        self.mock_helper.create_adaptive_mask.return_value = Mock()

        region_size = (512, 512)
        box = [0, 0, 512, 512]
        region_img = Image.new('RGB', region_size)

        mask = self.detector.create_mask(region_size, box, region_img)

        # Verify create_adaptive_mask was called with person-specific feather ratio
        self.mock_helper.create_adaptive_mask.assert_called_once_with(
            region_size, box, feather_ratio=0.15
        )

    def test_adjust_steps_small_person(self):
        """Test step adjustment for small person detection"""
        box = np.array([100, 100, 200, 250])
        image_size = (512, 512)

        steps = self.detector.adjust_steps(box, image_size)

        # Small person should use base steps
        self.assertEqual(steps, 20)

    def test_adjust_steps_large_person(self):
        """Test step adjustment for large person detection (>50% of image)"""
        # Large detection covering more than 50% of image
        box = np.array([0, 0, 400, 400])
        image_size = (512, 512)

        steps = self.detector.adjust_steps(box, image_size)

        # Large person should have reduced steps (70% of base)
        # 20 * 0.7 = 14
        self.assertEqual(steps, 14)

    def test_adjust_steps_minimum_cap(self):
        """Test that steps don't go below minimum of 10"""
        self.config["steps"] = 12

        # Large detection covering more than 50% of image
        box = np.array([0, 0, 500, 500])
        image_size = (512, 512)

        steps = self.detector.adjust_steps(box, image_size)

        # Should be capped at 10 minimum (12 * 0.7 = 8.4, capped to 10)
        self.assertEqual(steps, 10)


if __name__ == '__main__':
    unittest.main()
