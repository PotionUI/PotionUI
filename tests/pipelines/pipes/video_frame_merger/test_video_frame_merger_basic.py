import pytest
import numpy as np
from PIL import Image
from src.pipelines.pipes.video_frame_merger.main import VideoFrameMergerPipe
from src.pipelines.contracts import PipeInput, IOType


class TestVideoFrameMergerPipeBasic:

    def test_pipe_initialization(self):
        """Test pipe initialization with default config"""
        pipe = VideoFrameMergerPipe({})

        assert pipe.name == "video_frame_merger"
        assert "Merge image frames into video files" in pipe.description

        # Check default config
        config = pipe.get_default_config()
        assert config["fps"] == 30.0
        assert config["codec"] == "mp4v"
        assert config["output_format"] == "mp4"
        assert config["loop_count"] == 1
        assert config["reverse"] == False
        assert config["fade_in"] == 0
        assert config["fade_out"] == 0
        assert config["resize_mode"] == "keep"

    def test_pipe_initialization_with_custom_config(self):
        """Test pipe initialization with custom config"""
        custom_config = {
            "fps": 24.0,
            "loop_count": 2,
            "reverse": True
        }
        pipe = VideoFrameMergerPipe(custom_config)

        assert pipe.config["fps"] == 24.0
        assert pipe.config["loop_count"] == 2
        assert pipe.config["reverse"] == True
        # Default values are not automatically merged in BasePipe
        assert "codec" not in pipe.config

    def test_configuration_spec(self):
        """Test configuration specification"""
        specs = VideoFrameMergerPipe.configuration()

        spec_names = [spec.name for spec in specs]
        expected_names = [
            "fps", "codec", "output_format", "loop_count",
            "reverse", "fade_in", "fade_out", "resize_mode", "target_width", "target_height"
        ]

        for name in expected_names:
            assert name in spec_names

        # Check fps spec details
        fps_spec = next(spec for spec in specs if spec.name == "fps")
        assert fps_spec.param_type == float
        assert fps_spec.default == 30.0
        assert fps_spec.min_value == 1.0
        assert fps_spec.max_value == 240.0

        # Check codec choices
        codec_spec = next(spec for spec in specs if spec.name == "codec")
        assert "mp4v" in codec_spec.choices
        assert "xvid" in codec_spec.choices

        # Check resize_mode choices
        resize_spec = next(spec for spec in specs if spec.name == "resize_mode")
        assert "keep" in resize_spec.choices
        assert "fit" in resize_spec.choices
        assert "fill" in resize_spec.choices
        assert "stretch" in resize_spec.choices

    def test_input_output_specs(self):
        """Test input and output specifications"""
        inputs = VideoFrameMergerPipe.inputs()
        outputs = VideoFrameMergerPipe.outputs()

        assert len(inputs) == 1
        input_names = [inp.name for inp in inputs]
        assert "image" in input_names

        # Check image input spec
        image_input = next(inp for inp in inputs if inp.name == "image")
        assert image_input.io_type == IOType.IMAGE
        assert image_input.required == True
        assert image_input.is_array == True

        assert len(outputs) == 1
        assert outputs[0].name == "video"
        assert outputs[0].io_type == IOType.VIDEO
        assert outputs[0].is_array == True

    def test_apply_fade_effect(self):
        """Test fade effect application"""
        pipe = VideoFrameMergerPipe({})

        # Create test frame
        frame = np.ones((100, 100, 3), dtype=np.uint8) * 255  # White frame

        # Test full opacity (no change)
        result = pipe.apply_fade_effect(frame, 1.0)
        np.testing.assert_array_equal(result, frame)

        # Test half opacity
        result = pipe.apply_fade_effect(frame, 0.5)
        expected = np.ones((100, 100, 3), dtype=np.uint8) * 127  # Should be ~50% gray
        np.testing.assert_allclose(result, expected, atol=2)

        # Test zero opacity (black)
        result = pipe.apply_fade_effect(frame, 0.0)
        expected = np.zeros((100, 100, 3), dtype=np.uint8)
        np.testing.assert_array_equal(result, expected)

        # Test values outside 0-1 range
        result = pipe.apply_fade_effect(frame, -0.5)  # Negative should clamp to 0
        expected = np.zeros((100, 100, 3), dtype=np.uint8)
        np.testing.assert_array_equal(result, expected)

    def test_resize_frame_keep(self):
        """Test frame resizing with 'keep' mode"""
        pipe = VideoFrameMergerPipe({})

        frame = Image.new('RGB', (100, 100), (255, 0, 0))
        result = pipe.resize_frame(frame, (200, 200), "keep")

        # Should return original frame unchanged
        assert result.size == (100, 100)
        assert result == frame

    def test_resize_frame_stretch(self):
        """Test frame resizing with 'stretch' mode"""
        pipe = VideoFrameMergerPipe({})

        frame = Image.new('RGB', (100, 100), (255, 0, 0))
        result = pipe.resize_frame(frame, (200, 150), "stretch")

        # Should resize to exact dimensions
        assert result.size == (200, 150)

    def test_resize_frame_fit(self):
        """Test frame resizing with 'fit' mode"""
        pipe = VideoFrameMergerPipe({})

        # Create a red square frame
        frame = Image.new('RGB', (100, 100), (255, 0, 0))
        result = pipe.resize_frame(frame, (200, 300), "fit")

        # Should fit inside target dimensions
        assert result.size == (200, 300)

        # The result should have the red square centered with black borders
        result_array = np.array(result)

        # Check that there are black pixels (borders)
        has_black = np.any((result_array == [0, 0, 0]).all(axis=2))
        assert has_black, "Expected black borders in fit mode"

    def test_resize_frame_fill(self):
        """Test frame resizing with 'fill' mode"""
        pipe = VideoFrameMergerPipe({})

        # Create a red rectangular frame
        frame = Image.new('RGB', (200, 100), (255, 0, 0))
        result = pipe.resize_frame(frame, (100, 100), "fill")

        # Should fill exact target dimensions
        assert result.size == (100, 100)

        # Result should be all red (cropped but no black borders)
        result_array = np.array(result)
        is_all_red = np.all((result_array == [255, 0, 0]).all(axis=2))
        assert is_all_red, "Expected all red pixels in fill mode"

    def test_create_video_empty_frames(self):
        """Test video creation with empty frame list"""
        pipe = VideoFrameMergerPipe({})

        # Mock generation outputs
        from unittest.mock import Mock
        mock_outputs = Mock()

        # Try to create video with empty frames
        result = pipe.create_video_from_frames([], mock_outputs)

        # Should return None
        assert result is None