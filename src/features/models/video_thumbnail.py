import logging
import random
from typing import Optional, List
from pathlib import Path
from PIL import Image

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError as e:
    logging.warning(f"OpenCV not available: {e}. Video thumbnail generation will be disabled.")
    CV2_AVAILABLE = False

logger = logging.getLogger(__name__)

_warned_cv2_unavailable_info = False

class VideoThumbnailService:
    """Service for extracting thumbnails from video files"""

    def __init__(self, thumbnail_size: tuple = (512, 512)):
        self.thumbnail_size = thumbnail_size
        self.quality = 85

    def extract_random_frame(self, video_path: str) -> Optional['np.ndarray']:
        """
        Extract a random frame from a video file
        Returns: numpy array representing the frame, or None if failed
        """
        if not CV2_AVAILABLE:
            logger.error("OpenCV not available, cannot extract video frames")
            return None

        try:
            # Open video file
            cap = cv2.VideoCapture(video_path)

            if not cap.isOpened():
                logger.error(f"Could not open video file: {video_path}")
                return None

            # Get total frame count
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            if total_frames <= 0:
                logger.error(f"Video has no frames: {video_path}")
                cap.release()
                return None

            # Choose a random frame (avoid first and last 10% of video for better frames)
            start_frame = int(total_frames * 0.1)
            end_frame = int(total_frames * 0.9)

            if start_frame >= end_frame:
                # Video too short, use middle frame
                random_frame = total_frames // 2
            else:
                random_frame = random.randint(start_frame, end_frame)

            # Seek to random frame
            cap.set(cv2.CAP_PROP_POS_FRAMES, random_frame)

            # Read the frame
            ret, frame = cap.read()
            cap.release()

            if not ret or frame is None:
                logger.error(f"Could not read frame {random_frame} from video: {video_path}")
                return None

            # Convert BGR to RGB (OpenCV uses BGR by default)
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            logger.debug(f"Successfully extracted frame {random_frame}/{total_frames} from {video_path}")
            return frame_rgb

        except Exception as e:
            logger.error(f"Error extracting frame from video {video_path}: {e}")
            return None

    def create_thumbnail_from_frame(self, frame: 'np.ndarray', output_path: str) -> bool:
        """
        Create a thumbnail from a video frame and save it
        """
        try:
            # Convert numpy array to PIL Image
            pil_image = Image.fromarray(frame)

            # Create thumbnail while preserving aspect ratio
            pil_image.thumbnail(self.thumbnail_size, Image.Resampling.LANCZOS)

            # Save as JPEG
            pil_image.save(output_path, 'JPEG', quality=self.quality, optimize=True)

            logger.debug(f"Successfully created thumbnail: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error creating thumbnail from frame: {e}")
            return False

    def create_thumbnail_from_video(self, video_path: str, output_path: str) -> bool:
        """
        Extract a random frame from video and create thumbnail
        """
        if not CV2_AVAILABLE:
            logger.error("OpenCV not available, cannot create thumbnail from video")
            return False

        try:
            # Extract random frame
            frame = self.extract_random_frame(video_path)
            if frame is None:
                return False

            # Create thumbnail from frame
            return self.create_thumbnail_from_frame(frame, output_path)

        except Exception as e:
            logger.error(f"Error creating thumbnail from video {video_path}: {e}")
            return False

    def extract_multiple_frames(self, video_path: str, num_frames: int = 3) -> List['np.ndarray']:
        """
        Extract multiple random frames from a video (for better thumbnail selection)
        """
        if not CV2_AVAILABLE:
            logger.error("OpenCV not available, cannot extract multiple frames")
            return []

        frames = []

        try:
            cap = cv2.VideoCapture(video_path)

            if not cap.isOpened():
                logger.error(f"Could not open video file: {video_path}")
                return frames

            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

            if total_frames <= 0:
                cap.release()
                return frames

            # Calculate frame positions to extract
            start_frame = int(total_frames * 0.1)
            end_frame = int(total_frames * 0.9)

            if start_frame >= end_frame:
                frame_positions = [total_frames // 2]
            else:
                # Generate random frame positions
                frame_positions = []
                for _ in range(num_frames):
                    pos = random.randint(start_frame, end_frame)
                    frame_positions.append(pos)

            # Extract frames
            for frame_pos in frame_positions:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)
                ret, frame = cap.read()

                if ret and frame is not None:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    frames.append(frame_rgb)

            cap.release()
            logger.debug(f"Extracted {len(frames)} frames from {video_path}")

        except Exception as e:
            logger.error(f"Error extracting multiple frames from {video_path}: {e}")

        return frames

    def select_best_frame(self, frames: List['np.ndarray']) -> Optional['np.ndarray']:
        """
        Select the best frame from a list based on simple heuristics
        (highest variance/contrast typically indicates more interesting content)
        """
        if not frames:
            return None

        if len(frames) == 1:
            return frames[0]

        try:
            best_frame = None
            best_score = -1

            for frame in frames:
                # Convert to grayscale for variance calculation
                gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

                # Calculate variance (higher variance = more contrast/detail)
                variance = np.var(gray)

                if variance > best_score:
                    best_score = variance
                    best_frame = frame

            logger.debug(f"Selected best frame with variance score: {best_score}")
            return best_frame

        except Exception as e:
            logger.error(f"Error selecting best frame: {e}")
            # Return first frame as fallback
            return frames[0] if frames else None

    def create_best_thumbnail_from_video(self, video_path: str, output_path: str, num_candidates: int = 3) -> bool:
        """
        Extract multiple frames and create thumbnail from the best one
        """
        if not CV2_AVAILABLE:
            logger.error("OpenCV not available, cannot create best thumbnail from video")
            return False

        try:
            # Extract multiple frames
            frames = self.extract_multiple_frames(video_path, num_candidates)

            if not frames:
                return False

            # Select best frame
            best_frame = self.select_best_frame(frames)

            if best_frame is None:
                return False

            # Create thumbnail from best frame
            return self.create_thumbnail_from_frame(best_frame, output_path)

        except Exception as e:
            logger.error(f"Error creating best thumbnail from video {video_path}: {e}")
            return False

    def get_video_info(self, video_path: str) -> dict:
        """Get basic information about a video file"""
        if not CV2_AVAILABLE:
            global _warned_cv2_unavailable_info
            if not _warned_cv2_unavailable_info:
                logger.warning("OpenCV not available, cannot get video info")
                _warned_cv2_unavailable_info = True
            return {}

        try:
            cap = cv2.VideoCapture(video_path)

            if not cap.isOpened():
                return {}

            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = frame_count / fps if fps > 0 else 0

            cap.release()

            return {
                'fps': fps,
                'frame_count': frame_count,
                'width': width,
                'height': height,
                'duration_seconds': duration
            }

        except Exception as e:
            logger.error(f"Error getting video info for {video_path}: {e}")
            return {}

# Global service instance
video_thumbnail_service = VideoThumbnailService()