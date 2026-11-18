import hashlib
from pathlib import Path

import numpy as np

from tests.platform.media_tools.media_factory import decode_video_rgb, run_ffmpeg, video_frame_count


def lossless_clip(path: Path, frames=20, fps=10, size=(64, 48), audio=True, pattern="testsrc") -> Path:
    args = ["-f", "lavfi", "-i", f"{pattern}=size={size[0]}x{size[1]}:rate={fps}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={frames / fps}"]
    args += ["-frames:v", str(frames), "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "64k", "-shortest"]
    run_ffmpeg(*args, str(path))
    assert video_frame_count(path) == frames
    return path


def frames_of(path: Path, size=(64, 48)) -> np.ndarray:
    return decode_video_rgb(path, size[0], size[1])


def frame_digests(path: Path, size=(64, 48)):
    return [hashlib.sha256(frame.tobytes()).hexdigest() for frame in frames_of(path, size)]


def count_frames(path: Path) -> int:
    return video_frame_count(path)
