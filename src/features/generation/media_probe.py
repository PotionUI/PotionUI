"""Shared media metadata probing - video (width/height/duration/fps).

Extracted from ``video_handler.py``'s ffprobe/cv2 dimension probe so the
same probe backs two callers that need it independently: the
video generation output handler (server-authored videos, persisted to the
``files`` table -- see migration 086) and the media upload path (user-supplied
videos, returned directly in the upload response since uploads have no
``files`` row to hang metadata off). Neither caller should grow its own
ffprobe/cv2 dance.

Mesh (glTF-binary) probing lives in
``src.platform.filesystem.mesh_formats`` instead of here: it backs
`FileStore`, which is `platform` code and cannot import anything under
`src.features`.

Audio duration uses ``soundfile`` rather than the ffprobe/cv2 dance above:
every format PotionUI accepts (see ``src.platform.filesystem.audio_formats``)
is already read generically by libsndfile, so there is no per-container
parsing to fall back on the way video needs opencv as a second reader.
"""

from __future__ import annotations

import json
import logging
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_video_dimensions(video_path: str) -> Tuple[Optional[int], Optional[int]]:
    """Get (width, height) for a video using ffprobe, falling back to opencv."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-select_streams', 'v:0', video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get('streams') or []
            if streams:
                width = streams[0].get('width')
                height = streams[0].get('height')
                if width and height:
                    logger.debug(f"Got video dimensions via ffprobe: {width}x{height}")
                    return width, height
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        logger.debug(f"ffprobe failed, trying opencv: {e}")

    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cap.release()
            if width > 0 and height > 0:
                logger.debug(f"Got video dimensions via opencv: {width}x{height}")
                return width, height
    except ImportError:
        logger.debug("OpenCV not available for video dimension detection")
    except Exception as e:
        logger.debug(f"OpenCV video dimension detection failed: {e}")

    logger.warning(f"Could not determine video dimensions for: {video_path}")
    return None, None


def _parse_frame_rate(raw: Optional[str]) -> Optional[float]:
    """Parse ffprobe's ``r_frame_rate`` (e.g. ``"24/1"``, ``"24000/1001"``)."""
    if not raw:
        return None
    try:
        if '/' in raw:
            num, den = raw.split('/', 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        return float(raw)
    except (ValueError, ZeroDivisionError):
        return None


def get_video_duration_fps(video_path: str) -> Tuple[Optional[float], Optional[float]]:
    """Get (duration_seconds, fps) for a video using ffprobe, falling back to opencv."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', '-select_streams', 'v:0', video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get('streams') or []
            stream = streams[0] if streams else {}
            fps = _parse_frame_rate(stream.get('r_frame_rate'))

            duration = None
            for raw_duration in (stream.get('duration'), data.get('format', {}).get('duration')):
                if raw_duration is None:
                    continue
                try:
                    duration = float(raw_duration)
                    break
                except (TypeError, ValueError):
                    continue

            if duration is not None or fps is not None:
                logger.debug(f"Got video duration/fps via ffprobe: {duration}s @ {fps}fps")
                return duration, fps
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        logger.debug(f"ffprobe failed, trying opencv: {e}")

    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS) or None
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
            duration = frame_count / fps if fps and frame_count > 0 else None
            if duration is not None or fps is not None:
                logger.debug(f"Got video duration/fps via opencv: {duration}s @ {fps}fps")
                return duration, fps
    except ImportError:
        logger.debug("OpenCV not available for video duration/fps detection")
    except Exception as e:
        logger.debug(f"OpenCV video duration/fps detection failed: {e}")

    logger.warning(f"Could not determine video duration/fps for: {video_path}")
    return None, None


def get_audio_duration_seconds(audio_path: str) -> Optional[float]:
    """Get duration (in seconds) for an audio file using ``soundfile``.

    Best-effort, matching the video probe's degrade-gracefully contract: a
    missing dependency, a corrupt file, or an unsupported container must not
    raise - it returns ``None`` and lets the caller (upload metadata probing,
    the audio generation output handler) carry on without a duration rather
    than fail the whole upload/save over cosmetic metadata.
    """
    try:
        import soundfile as sf
    except ImportError:
        logger.debug("soundfile not available for audio duration detection")
        return None

    try:
        info = sf.info(audio_path)
    except Exception as e:
        logger.warning(f"Could not determine audio duration for {audio_path}: {e}")
        return None

    if not info.samplerate:
        return None
    return info.frames / info.samplerate

