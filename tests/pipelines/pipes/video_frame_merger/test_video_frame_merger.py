import pytest
import tempfile
import numpy as np
from pathlib import Path
from PIL import Image
from unittest.mock import Mock, patch

cv2 = pytest.importorskip("cv2", reason="cv2 required by video frame merging", exc_type=ImportError)

from src.pipelines.pipes.video_frame_merger.main import VideoFrameMergerPipe
from src.pipelines.contracts import PipeInput, IOType


class TestVideoFrameMergerPipe:

    def create_test_frames(self, count: int = 5, size: tuple = (320, 240)) -> list:
        """Create test PIL Image frames"""
        frames = []
        width, height = size

        for i in range(count):
            # Create frame with changing color
            frame_array = np.zeros((height, width, 3), dtype=np.uint8)

            # Create gradient effect
            red_value = int(255 * (i / count))
            blue_value = int(255 * (1 - i / count))

            frame_array[:, :, 0] = red_value   # Red channel
            frame_array[:, :, 2] = blue_value  # Blue channel

            # Add some pattern
            frame_array[i*10:(i+1)*10, :, 1] = 255  # Green stripes

            # Convert to PIL Image
            pil_image = Image.fromarray(frame_array, 'RGB')
            frames.append(pil_image)

        return frames

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
        np.testing.assert_allclose(result, expected, atol=1)

        # Test zero opacity (black)
        result = pipe.apply_fade_effect(frame, 0.0)
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

        frame = Image.new('RGB', (100, 100), (255, 0, 0))
        result = pipe.resize_frame(frame, (200, 300), "fit")

        # Should fit inside target dimensions
        assert result.size == (200, 300)

        # Check that it has black borders (letterboxing)
        # The original square should be centered

    def test_create_video_basic(self):
        """Test basic video creation from frames"""
        # Create test frames
        frames = self.create_test_frames(count=10, size=(320, 240))

        # Create pipe with basic config
        config = {"fps": 5.0, "codec": "mp4v"}
        pipe = VideoFrameMergerPipe(config)

        # Mock generation outputs
        mock_outputs = Mock()

        # Create video
        video_path = pipe.create_video_from_frames(frames, mock_outputs)

        try:
            # Check video was created
            assert video_path is not None
            assert Path(video_path).exists()
            assert Path(video_path).stat().st_size > 0

            # Verify video properties using OpenCV
            cap = cv2.VideoCapture(video_path)
            assert cap.isOpened()

            # Check frame count and dimensions
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)

            cap.release()

            assert frame_count == len(frames)
            assert width == 320
            assert height == 240
            assert abs(fps - 5.0) < 1.0  # Allow some tolerance

            # Verify mock was called
            assert mock_outputs.call_count > 0

        finally:
            # Cleanup
            if video_path:
                Path(video_path).unlink(missing_ok=True)

    def test_create_video_with_looping(self):
        """Test video creation with frame looping"""
        # Create test frames
        frames = self.create_test_frames(count=5, size=(160, 120))

        # Create pipe with looping config
        config = {"fps": 10.0, "loop_count": 3, "codec": "mp4v"}
        pipe = VideoFrameMergerPipe(config)

        # Mock generation outputs
        mock_outputs = Mock()

        # Create video
        video_path = pipe.create_video_from_frames(frames, mock_outputs)

        try:
            # Check video was created
            assert video_path is not None
            assert Path(video_path).exists()

            # Verify video has 3x the frames
            cap = cv2.VideoCapture(video_path)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()

            assert frame_count == len(frames) * 3

        finally:
            # Cleanup
            if video_path:
                Path(video_path).unlink(missing_ok=True)

    def test_create_video_with_reverse(self):
        """Test video creation with reverse frames"""
        # Create test frames
        frames = self.create_test_frames(count=5, size=(160, 120))

        # Create pipe with reverse config
        config = {"fps": 10.0, "reverse": True, "codec": "mp4v"}
        pipe = VideoFrameMergerPipe(config)

        # Mock generation outputs
        mock_outputs = Mock()

        # Create video
        video_path = pipe.create_video_from_frames(frames, mock_outputs)

        try:
            # Check video was created
            assert video_path is not None
            assert Path(video_path).exists()

            # With reverse, we should have: original + reverse (excluding first and last)
            # 5 frames + 3 reverse frames = 8 total
            cap = cv2.VideoCapture(video_path)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()

            expected_frames = len(frames) + (len(frames) - 2)  # original + reverse without duplicates
            assert frame_count == expected_frames

        finally:
            # Cleanup
            if video_path:
                Path(video_path).unlink(missing_ok=True)

    def test_process_basic(self):
        """Test basic frame processing"""
        # Create test frames
        frames = self.create_test_frames(count=8, size=(240, 180))

        # Create pipe
        config = {"fps": 15.0}
        pipe = VideoFrameMergerPipe(config)

        # Create pipe input
        pipe_input = PipeInput(input={"image": frames})

        # Mock generation outputs
        mock_outputs = Mock()

        # Process
        output = pipe.process(pipe_input, mock_outputs)

        try:
            # Check output
            assert "video" in output.output
            videos = output.output["video"]

            assert isinstance(videos, list)
            assert len(videos) == 1

            video_path = videos[0]
            assert Path(video_path).exists()

            # Verify mock was called
            assert mock_outputs.call_count > 0

        finally:
            # Cleanup
            if "video" in output.output:
                for video_path in output.output["video"]:
                    if video_path and Path(video_path).exists():
                        Path(video_path).unlink(missing_ok=True)

    def test_process_no_frames(self):
        """Test processing with no input frames"""
        # Create pipe
        pipe = VideoFrameMergerPipe({})

        # Create pipe input with no frames
        pipe_input = PipeInput(input={})

        # Mock generation outputs
        mock_outputs = Mock()

        # Process
        output = pipe.process(pipe_input, mock_outputs)

        # Check output is empty
        assert output.output["video"] == []

    def test_process_with_metadata(self):
        """Test processing with frame metadata"""
        # Create test frames and metadata
        frames = self.create_test_frames(count=6, size=(200, 150))
        metadata = [
            {
                "video_path": "/test/video1.mp4",
                "video_name": "video1.mp4",
                "extracted_frames": 6,
                "frame_rate": 2.0
            }
        ]

        # Create pipe
        config = {"fps": 20.0}
        pipe = VideoFrameMergerPipe(config)

        # Create pipe input with metadata
        pipe_input = PipeInput(input={
            "image": frames,
            "frame_metadata": metadata
        })

        # Mock generation outputs
        mock_outputs = Mock()

        # Process
        output = pipe.process(pipe_input, mock_outputs)

        try:
            # Check output
            assert "video" in output.output
            videos = output.output["video"]

            assert len(videos) == 1
            assert Path(videos[0]).exists()

        finally:
            # Cleanup
            if "video" in output.output:
                for video_path in output.output["video"]:
                    if video_path and Path(video_path).exists():
                        Path(video_path).unlink(missing_ok=True)

    def test_create_video_empty_frames(self):
        """Test video creation with empty frame list"""
        # Create pipe
        pipe = VideoFrameMergerPipe({})

        # Mock generation outputs
        mock_outputs = Mock()

        # Try to create video with empty frames
        result = pipe.create_video_from_frames([], mock_outputs)

        # Should return None
        assert result is None