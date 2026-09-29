import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from src.features.content_safety.errors import ContentCheckUnavailable
from src.features.content_safety.constants import (
    DEFAULT_VIDEO_FRAMES,
    MAX_VIDEO_FRAMES,
    SETTING_VIDEO_FRAMES,
)

logger = logging.getLogger(__name__)

_FFMPEG_TIMEOUT_SECONDS = 60


def frame_times(duration: float, count: int) -> List[float]:
    count = max(1, count)
    if count == 1:
        return [duration * 0.5]
    step = 0.8 / (count - 1)
    return [duration * (0.1 + index * step) for index in range(count)]


def build_frame_command(video_path: str, times: Sequence[float], out_dir: Path) -> List[str]:
    command = ["ffmpeg", "-y", "-nostdin", "-v", "error"]
    for moment in times:
        command += ["-ss", f"{moment:.3f}", "-i", video_path]
    for index in range(len(times)):
        command += ["-map", f"{index}:v:0", "-frames:v", "1", "-q:v", "3", str(out_dir / f"frame_{index}.jpg")]
    return command


def extract_video_frames(video_path: str, count: int) -> List[Any]:
    from PIL import Image

    from src.features.generation import media_probe

    duration, _ = media_probe.get_video_duration_fps(video_path)
    if not duration or duration <= 0:
        raise ContentCheckUnavailable("video duration could not be read")
    times = frame_times(duration, count)
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        try:
            result = subprocess.run(
                build_frame_command(video_path, times, out_dir),
                capture_output=True,
                timeout=_FFMPEG_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise ContentCheckUnavailable(f"video frames could not be extracted: {exc}") from exc
        if result.returncode != 0:
            raise ContentCheckUnavailable("video frames could not be extracted")
        frames = []
        for index in range(len(times)):
            frame_path = out_dir / f"frame_{index}.jpg"
            if not frame_path.is_file():
                continue
            with Image.open(frame_path) as handle:
                handle.load()
                frames.append(handle.convert("RGB"))
    if not frames:
        raise ContentCheckUnavailable("no video frames were extracted")
    return frames


def nsfw_score(ratings: dict) -> float:
    return float(ratings.get("questionable", 0.0)) + float(ratings.get("explicit", 0.0))


class ContentGate:
    def __init__(
        self,
        tagger: Any,
        settings: Any,
        frame_extractor: Optional[Callable[[str, int], List[Any]]] = None,
    ):
        self.tagger = tagger
        self.settings = settings
        self._extract_frames = frame_extractor or extract_video_frames

    @property
    def rater(self) -> str:
        return self.tagger.provenance

    def available(self) -> bool:
        return bool(self.tagger.has_weights())

    def frame_count(self) -> int:
        try:
            value = int(self.settings.get_setting(SETTING_VIDEO_FRAMES, DEFAULT_VIDEO_FRAMES))
        except (TypeError, ValueError):
            value = DEFAULT_VIDEO_FRAMES
        return max(1, min(MAX_VIDEO_FRAMES, value))

    def rate_images(self, images: Sequence[Any]) -> List[float]:
        if not images:
            return []
        if not self.available():
            raise ContentCheckUnavailable("tagger weights are not present")
        try:
            results = self.tagger.tag_images(list(images))
        except ContentCheckUnavailable:
            raise
        except Exception as exc:
            logger.exception("content rating failed")
            raise ContentCheckUnavailable(str(exc)) from exc
        if len(results) != len(images):
            raise ContentCheckUnavailable("tagger returned the wrong number of results")
        return [nsfw_score(result.ratings) for result in results]

    def rate_image(self, image: Any) -> float:
        return self.rate_images([image])[0]

    def rate_video(self, video_path: str) -> float:
        try:
            frames = self._extract_frames(str(video_path), self.frame_count())
        except ContentCheckUnavailable:
            raise
        except Exception as exc:
            logger.exception("video frame extraction failed")
            raise ContentCheckUnavailable(str(exc)) from exc
        return max(self.rate_images(frames))

    def rate_image_file(self, path: str) -> float:
        from PIL import Image

        try:
            with Image.open(path) as handle:
                handle.load()
                image = handle.convert("RGB")
        except Exception as exc:
            raise ContentCheckUnavailable(str(exc)) from exc
        return self.rate_image(image)
