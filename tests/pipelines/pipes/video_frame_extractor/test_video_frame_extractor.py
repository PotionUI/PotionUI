import pytest
import tempfile
import numpy as np
from pathlib import Path
from PIL import Image
from unittest.mock import Mock, patch

cv2 = pytest.importorskip("cv2", reason="cv2 required by video frame extraction", exc_type=ImportError)

from src.pipelines.pipes.video_frame_extractor.main import VideoFrameExtractorPipe
from src.pipelines.contracts import PipeInput, IOType


class TestVideoFrameExtractorPipe:

    def create_test_video(self, duration_sec: float = 5.0, fps: float = 30.0) -> str:
        """Create a test video file"""
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
            video_path = temp_file.name

        # Create video writer
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        width, height = 640, 480
        writer = cv2.VideoWriter(video_path, fourcc, fps, (width, height))

        total_frames = int(duration_sec * fps)

        for i in range(total_frames):
            # Create frame with changing color (red to blue gradient)
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            red_value = int(255 * (1 - i / total_frames))
            blue_value = int(255 * (i / total_frames))
            frame[:, :, 0] = blue_value  # BGR format
            frame[:, :, 2] = red_value

            # Add frame number text
            cv2.putText(frame, f"Frame {i}", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

            writer.write(frame)

        writer.release()
        return video_path

    def test_pipe_initialization(self):
        """Test pipe initialization with default config"""
        pipe = VideoFrameExtractorPipe({})

        assert pipe.name == "video_frame_extractor"
        assert "Extract frames from video files" in pipe.description

        # Check default config
        config = pipe.get_default_config()
        assert config["frame_rate"] == 1
        assert config["start_time"] == 0
        assert config["end_time"] == -1
        assert config["max_frames"] == -1
        assert config["frame_format"] == "png"
        assert config["quality"] == 95

    def test_configuration_spec(self):
        """Test configuration specification"""
        specs = VideoFrameExtractorPipe.configuration()

        spec_names = [spec.name for spec in specs]
        assert "frame_rate" in spec_names
        assert "start_time" in spec_names
        assert "end_time" in spec_names
        assert "max_frames" in spec_names
        assert "frame_format" in spec_names
        assert "quality" in spec_names

        # Check frame_rate spec details
        frame_rate_spec = next(spec for spec in specs if spec.name == "frame_rate")
        assert frame_rate_spec.param_type == float
        assert frame_rate_spec.default == 1.0
        assert frame_rate_spec.min_value == 0.1
        assert frame_rate_spec.max_value == 60.0

    def test_input_output_specs(self):
        """Test input and output specifications"""
        inputs = VideoFrameExtractorPipe.inputs()
        outputs = VideoFrameExtractorPipe.outputs()

        assert len(inputs) == 1
        assert inputs[0].name == "video"
        assert inputs[0].io_type == IOType.VIDEO
        assert inputs[0].required == True
        assert inputs[0].is_array == True

        assert len(outputs) == 2
        output_names = [output.name for output in outputs]
        assert "image" in output_names
        assert "frame_metadata" in output_names

    def test_extract_frames_basic(self):
        """Test basic frame extraction"""
        # Create test video
        video_path = self.create_test_video(duration_sec=2.0, fps=10.0)

        try:
            # Create pipe with basic config
            config = {"frame_rate": 2.0}  # Extract 2 frames per second
            pipe = VideoFrameExtractorPipe(config)

            # Mock generation outputs
            mock_outputs = Mock()

            # Extract frames
            frames = pipe.extract_frames_from_video(video_path, mock_outputs)

            # Should extract approximately 4 frames (2 seconds * 2 fps)
            assert len(frames) >= 3
            assert len(frames) <= 5

            # Check frames are PIL Images
            for frame in frames:
                assert isinstance(frame, Image.Image)
                assert frame.size == (640, 480)
                assert frame.mode == "RGB"

            # Verify mock was called for progress updates
            assert mock_outputs.call_count > 0

        finally:
            # Cleanup
            Path(video_path).unlink(missing_ok=True)

    def test_extract_frames_with_time_range(self):
        """Test frame extraction with time range"""
        # Create test video
        video_path = self.create_test_video(duration_sec=5.0, fps=10.0)

        try:
            # Create pipe with time range config
            config = {
                "frame_rate": 1.0,
                "start_time": 1.0,  # Start at 1 second
                "end_time": 3.0,    # End at 3 seconds
            }
            pipe = VideoFrameExtractorPipe(config)

            # Mock generation outputs
            mock_outputs = Mock()

            # Extract frames
            frames = pipe.extract_frames_from_video(video_path, mock_outputs)

            # Should extract approximately 2 frames (2 seconds * 1 fps)
            assert len(frames) >= 1
            assert len(frames) <= 3

        finally:
            # Cleanup
            Path(video_path).unlink(missing_ok=True)

    def test_extract_frames_with_max_frames(self):
        """Test frame extraction with max frames limit"""
        # Create test video
        video_path = self.create_test_video(duration_sec=5.0, fps=10.0)

        try:
            # Create pipe with max frames limit
            config = {
                "frame_rate": 5.0,   # Would normally extract many frames
                "max_frames": 3      # But limit to 3 frames
            }
            pipe = VideoFrameExtractorPipe(config)

            # Mock generation outputs
            mock_outputs = Mock()

            # Extract frames
            frames = pipe.extract_frames_from_video(video_path, mock_outputs)

            # Should extract exactly 3 frames due to limit
            assert len(frames) == 3

        finally:
            # Cleanup
            Path(video_path).unlink(missing_ok=True)

    def test_process_single_video(self):
        """Test processing a single video"""
        # Create test video
        video_path = self.create_test_video(duration_sec=1.0, fps=5.0)

        try:
            # Create pipe
            config = {"frame_rate": 1.0}
            pipe = VideoFrameExtractorPipe(config)

            # Create pipe input
            pipe_input = PipeInput(input={"video": [video_path]})

            # Mock generation outputs
            mock_outputs = Mock()

            # Process
            output = pipe.process(pipe_input, mock_outputs)

            # Check output
            assert "image" in output.output
            assert "frame_metadata" in output.output

            images = output.output["image"]
            metadata = output.output["frame_metadata"]

            assert isinstance(images, list)
            assert len(images) > 0
            assert isinstance(metadata, list)
            assert len(metadata) == 1  # One video processed

            # Check metadata structure
            video_metadata = metadata[0]
            assert "video_path" in video_metadata
            assert "video_name" in video_metadata
            assert "extracted_frames" in video_metadata
            assert "frame_rate" in video_metadata
            assert video_metadata["frame_rate"] == 1.0

        finally:
            # Cleanup
            Path(video_path).unlink(missing_ok=True)

    def test_process_multiple_videos(self):
        """Test processing multiple videos"""
        # Create two test videos
        video_path1 = self.create_test_video(duration_sec=1.0, fps=5.0)
        video_path2 = self.create_test_video(duration_sec=1.5, fps=5.0)

        try:
            # Create pipe
            config = {"frame_rate": 2.0}
            pipe = VideoFrameExtractorPipe(config)

            # Create pipe input with multiple videos
            pipe_input = PipeInput(input={"video": [video_path1, video_path2]})

            # Mock generation outputs
            mock_outputs = Mock()

            # Process
            output = pipe.process(pipe_input, mock_outputs)

            # Check output
            images = output.output["image"]
            metadata = output.output["frame_metadata"]

            assert len(metadata) == 2  # Two videos processed
            assert len(images) > 2     # Should have frames from both videos

        finally:
            # Cleanup
            Path(video_path1).unlink(missing_ok=True)
            Path(video_path2).unlink(missing_ok=True)

    def test_process_no_video_input(self):
        """Test processing with no video input"""
        # Create pipe
        pipe = VideoFrameExtractorPipe({})

        # Create pipe input with no videos
        pipe_input = PipeInput(input={})

        # Mock generation outputs
        mock_outputs = Mock()

        # Process
        output = pipe.process(pipe_input, mock_outputs)

        # Check output is empty
        assert output.output["image"] == []
        assert output.output["frame_metadata"] == []

    def test_invalid_video_path(self):
        """Test handling of invalid video path"""
        # Create pipe
        pipe = VideoFrameExtractorPipe({})

        # Mock generation outputs
        mock_outputs = Mock()

        # Try to extract from non-existent file
        frames = pipe.extract_frames_from_video("/non/existent/video.mp4", mock_outputs)

        # Should return empty list
        assert frames == []

        # Should have called mock_outputs with error
        assert mock_outputs.call_count > 0