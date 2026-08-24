import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np

cv2 = pytest.importorskip("cv2", reason="cv2 required by VideoThumbnailService frame extraction", exc_type=ImportError)  # noqa: F841

from src.features.models.video_thumbnail import VideoThumbnailService

class TestVideoThumbnailService:

    def setup_method(self):
        self.service = VideoThumbnailService()

    def test_init(self):
        """Test VideoThumbnailService initialization"""
        assert self.service.thumbnail_size == (512, 512)
        assert self.service.quality == 85

    def test_init_custom_size(self):
        """Test VideoThumbnailService initialization with custom size"""
        service = VideoThumbnailService(thumbnail_size=(256, 256))
        assert service.thumbnail_size == (256, 256)

    @patch('cv2.VideoCapture')
    def test_extract_random_frame_success(self, mock_capture):
        """Test successful frame extraction from video"""
        # Mock video capture
        mock_cap = MagicMock()
        mock_capture.return_value = mock_cap
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 100  # 100 frames
        mock_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))

        # Mock cv2.cvtColor
        with patch('cv2.cvtColor') as mock_cvtColor:
            mock_cvtColor.return_value = np.zeros((480, 640, 3), dtype=np.uint8)

            result = self.service.extract_random_frame("test_video.mp4")

            assert result is not None
            assert isinstance(result, np.ndarray)
            mock_cap.set.assert_called()  # Should seek to random frame
            mock_cap.read.assert_called_once()
            mock_cap.release.assert_called_once()

    @patch('cv2.VideoCapture')
    def test_extract_random_frame_cannot_open(self, mock_capture):
        """Test frame extraction when video cannot be opened"""
        mock_cap = MagicMock()
        mock_capture.return_value = mock_cap
        mock_cap.isOpened.return_value = False

        result = self.service.extract_random_frame("nonexistent.mp4")

        assert result is None

    @patch('cv2.VideoCapture')
    def test_extract_random_frame_no_frames(self, mock_capture):
        """Test frame extraction when video has no frames"""
        mock_cap = MagicMock()
        mock_capture.return_value = mock_cap
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 0  # 0 frames

        result = self.service.extract_random_frame("empty_video.mp4")

        assert result is None
        mock_cap.release.assert_called_once()

    def test_create_thumbnail_from_frame_success(self):
        """Test successful thumbnail creation from frame"""
        # Create a test frame
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            try:
                result = self.service.create_thumbnail_from_frame(frame, tmp.name)

                assert result is True
                assert Path(tmp.name).exists()
                assert Path(tmp.name).stat().st_size > 0
            finally:
                # Cleanup
                if Path(tmp.name).exists():
                    Path(tmp.name).unlink()

    def test_create_thumbnail_from_frame_invalid_path(self):
        """Test thumbnail creation with invalid output path"""
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

        result = self.service.create_thumbnail_from_frame(frame, "/invalid/path/thumbnail.jpg")

        assert result is False

    @patch('src.features.models.video_thumbnail.VideoThumbnailService.extract_random_frame')
    @patch('src.features.models.video_thumbnail.VideoThumbnailService.create_thumbnail_from_frame')
    def test_create_thumbnail_from_video_success(self, mock_create_thumb, mock_extract_frame):
        """Test successful thumbnail creation from video"""
        # Mock frame extraction
        test_frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        mock_extract_frame.return_value = test_frame
        mock_create_thumb.return_value = True

        result = self.service.create_thumbnail_from_video("test.mp4", "output.jpg")

        assert result is True
        mock_extract_frame.assert_called_once_with("test.mp4")
        mock_create_thumb.assert_called_once_with(test_frame, "output.jpg")

    @patch('src.features.models.video_thumbnail.VideoThumbnailService.extract_random_frame')
    def test_create_thumbnail_from_video_no_frame(self, mock_extract_frame):
        """Test thumbnail creation when frame extraction fails"""
        mock_extract_frame.return_value = None

        result = self.service.create_thumbnail_from_video("test.mp4", "output.jpg")

        assert result is False

    @patch('cv2.VideoCapture')
    def test_extract_multiple_frames(self, mock_capture):
        """Test extraction of multiple frames"""
        mock_cap = MagicMock()
        mock_capture.return_value = mock_cap
        mock_cap.isOpened.return_value = True
        mock_cap.get.return_value = 100  # 100 frames
        mock_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))

        with patch('cv2.cvtColor') as mock_cvtColor:
            mock_cvtColor.return_value = np.zeros((480, 640, 3), dtype=np.uint8)

            frames = self.service.extract_multiple_frames("test.mp4", num_frames=3)

            assert len(frames) == 3
            assert all(isinstance(frame, np.ndarray) for frame in frames)

    def test_select_best_frame_empty_list(self):
        """Test best frame selection with empty list"""
        result = self.service.select_best_frame([])
        assert result is None

    def test_select_best_frame_single_frame(self):
        """Test best frame selection with single frame"""
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        frames = [frame]

        result = self.service.select_best_frame(frames)

        assert result is not None
        assert np.array_equal(result, frame)

    @patch('cv2.cvtColor')
    @patch('numpy.var')
    def test_select_best_frame_multiple_frames(self, mock_var, mock_cvtColor):
        """Test best frame selection with multiple frames"""
        frames = [
            np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8),
            np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8),
            np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        ]

        # Mock grayscale conversion
        mock_cvtColor.return_value = np.zeros((480, 640), dtype=np.uint8)
        # Mock variance calculation - make second frame have highest variance
        mock_var.side_effect = [10.0, 20.0, 15.0]

        result = self.service.select_best_frame(frames)

        assert result is not None
        assert np.array_equal(result, frames[1])  # Second frame should be selected

    @patch('cv2.VideoCapture')
    def test_get_video_info_success(self, mock_capture):
        """Test getting video information"""
        mock_cap = MagicMock()
        mock_capture.return_value = mock_cap
        mock_cap.isOpened.return_value = True
        mock_cap.get.side_effect = [30.0, 900, 1920, 1080]  # fps, frames, width, height

        info = self.service.get_video_info("test.mp4")

        expected = {
            'fps': 30.0,
            'frame_count': 900,
            'width': 1920,
            'height': 1080,
            'duration_seconds': 30.0  # 900 frames / 30 fps
        }

        assert info == expected

    @patch('cv2.VideoCapture')
    def test_get_video_info_cannot_open(self, mock_capture):
        """Test getting video info when video cannot be opened"""
        mock_cap = MagicMock()
        mock_capture.return_value = mock_cap
        mock_cap.isOpened.return_value = False

        info = self.service.get_video_info("nonexistent.mp4")

        assert info == {}