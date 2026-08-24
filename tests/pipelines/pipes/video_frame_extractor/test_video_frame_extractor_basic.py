import sys
import types

import pytest
from unittest.mock import patch
from PIL import Image
from src.pipelines.pipes.video_frame_extractor.main import VideoFrameExtractorPipe
from src.pipelines.contracts import PipeInput, IOType


class TestVideoFrameExtractorPipeBasic:

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
        assert config["frame_index"] is None

    def test_pipe_initialization_with_custom_config(self):
        """Test pipe initialization with custom config"""
        custom_config = {
            "frame_rate": 2.5,
            "start_time": 10.0,
            "max_frames": 100
        }
        pipe = VideoFrameExtractorPipe(custom_config)

        assert pipe.config["frame_rate"] == 2.5
        assert pipe.config["start_time"] == 10.0
        assert pipe.config["max_frames"] == 100
        # Default values are not automatically merged in BasePipe
        assert "frame_format" not in pipe.config

    def test_configuration_spec(self):
        """Test configuration specification"""
        specs = VideoFrameExtractorPipe.configuration()

        spec_names = [spec.name for spec in specs]
        expected_names = ["frame_rate", "start_time", "end_time", "max_frames", "frame_format", "quality", "frame_index"]

        for name in expected_names:
            assert name in spec_names

        # Check frame_rate spec details
        frame_rate_spec = next(spec for spec in specs if spec.name == "frame_rate")
        assert frame_rate_spec.param_type == float
        assert frame_rate_spec.default == 1.0
        assert frame_rate_spec.min_value == 0.1
        assert frame_rate_spec.max_value == 60.0

        # Check frame_format choices
        format_spec = next(spec for spec in specs if spec.name == "frame_format")
        assert "png" in format_spec.choices
        assert "jpg" in format_spec.choices

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

        # Check output types
        image_output = next(out for out in outputs if out.name == "image")
        assert image_output.io_type == IOType.IMAGE
        assert image_output.is_array == True

        metadata_output = next(out for out in outputs if out.name == "frame_metadata")
        assert metadata_output.io_type == IOType.DICT
        assert metadata_output.is_array == True

    def test_frame_index_bypasses_fps_loop_and_uses_shared_helper(self):
        """When frame_index is set, extraction should go through the shared
        extract_frame() helper for exactly one frame, not the fps-interval loop."""
        pipe = VideoFrameExtractorPipe({"frame_index": -1})
        fake_frame = Image.new("RGB", (4, 4), color=(1, 2, 3))
        outputs = []

        with patch(
            "src.pipelines.pipes._shared.media.frame_extract.extract_frame", return_value=fake_frame
        ) as mock_extract:
            frames = pipe.extract_frames_from_video("fake_video.mp4", outputs.append)

        mock_extract.assert_called_once_with("fake_video.mp4", -1)
        assert frames == [fake_frame]

    def test_frame_index_zero_is_respected_not_treated_as_falsy(self):
        """frame_index=0 (first frame) must still take the bypass path -- it's
        falsy in a naive `if self.config.get('frame_index')` check."""
        pipe = VideoFrameExtractorPipe({"frame_index": 0})
        fake_frame = Image.new("RGB", (4, 4))

        with patch(
            "src.pipelines.pipes._shared.media.frame_extract.extract_frame", return_value=fake_frame
        ) as mock_extract:
            pipe.extract_frames_from_video("fake_video.mp4", lambda _o: None)

        mock_extract.assert_called_once_with("fake_video.mp4", 0)

    def test_frame_index_none_does_not_use_shared_helper(self):
        """Default config (frame_index=None) must still take the fps-interval
        path, not the single-frame bypass."""
        pipe = VideoFrameExtractorPipe({})

        with patch("src.pipelines.pipes._shared.media.frame_extract.extract_frame") as mock_extract:
            # cv2 isn't importable in this environment, so the fps-loop path
            # will fail to open the (nonexistent) video and return []; the
            # only thing under test here is that it does NOT call extract_frame.
            pipe.extract_frames_from_video("fake_video.mp4", lambda _o: None)

        mock_extract.assert_not_called()

    def test_frame_index_error_reported_and_returns_empty(self):
        pipe = VideoFrameExtractorPipe({"frame_index": 5})
        outputs = []

        with patch(
            "src.pipelines.pipes._shared.media.frame_extract.extract_frame",
            side_effect=ValueError("frame index 5 out of range"),
        ):
            frames = pipe.extract_frames_from_video("fake_video.mp4", outputs.append)

        assert frames == []

    def test_fps_loop_seek_failure_falls_back_instead_of_truncating(self):
        """Seek-to-frame is codec-dependent (see frame_extract.py); a failing
        seek+read inside the fps-interval loop must fall back to the shared
        sequential-read helper, not break out and truncate extraction."""

        class FakeVideoCapture:
            def __init__(self, path):
                pass

            def isOpened(self):
                return True

            def get(self, prop):
                return {"FRAME_COUNT": 10, "FPS": 10.0}.get(prop, 0)

            def set(self, prop, val):
                pass

            def read(self):
                return False, None

            def release(self):
                pass

        fake_cv2 = types.ModuleType("cv2")
        fake_cv2.CAP_PROP_FRAME_COUNT = "FRAME_COUNT"
        fake_cv2.CAP_PROP_FPS = "FPS"
        fake_cv2.CAP_PROP_POS_FRAMES = "POS_FRAMES"
        fake_cv2.COLOR_BGR2RGB = "BGR2RGB"
        fake_cv2.VideoCapture = FakeVideoCapture
        fake_cv2.cvtColor = lambda frame, code: frame

        fake_frame = Image.new("RGB", (4, 4))
        pipe = VideoFrameExtractorPipe({"frame_rate": 10.0})

        with patch.dict(sys.modules, {"cv2": fake_cv2}), patch(
            "src.pipelines.pipes._shared.media.frame_extract.extract_frame",
            return_value=fake_frame,
        ) as mock_extract:
            frames = pipe.extract_frames_from_video("fake_video.mp4", lambda _o: None)

        assert len(frames) == 10
        assert mock_extract.call_count == 10