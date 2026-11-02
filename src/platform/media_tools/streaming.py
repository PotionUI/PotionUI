from __future__ import annotations

import logging
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import IO, Callable

import numpy as np

from src.platform.media_tools.fastpath import (
    CRF_BY_QUALITY,
    audio_codec_args,
    video_encoder_args,
    video_output_suffix,
)
from src.platform.media_tools.types import (
    FfmpegUnavailable,
    ToolCancelled,
    ToolContext,
    ToolFailed,
    ToolMedia,
)

logger = logging.getLogger(__name__)

CHANNELS = 3
DEFAULT_FPS = 24.0
MAX_FPS_DENOMINATOR = 1001
STDERR_TAIL_CHARS = 2000
STOP_GRACE_SECONDS = 1.0
CODEC_PROGRESS_CAP = 0.99
YUV_MATRIX = "bt709"

FrameTransform = Callable[[np.ndarray], np.ndarray]


def _rate(fps: float | None) -> str:
    ratio = Fraction(fps if fps and fps > 0 else DEFAULT_FPS).limit_denominator(MAX_FPS_DENOMINATOR)
    return f"{ratio.numerator}/{ratio.denominator}"


def _decode_command(ffmpeg: str, media: ToolMedia, width: int, height: int) -> list[str]:
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(media.path), "-map", "0:v:0", "-an", "-sn",
    ]
    if (width, height) != (media.width, media.height):
        command += ["-vf", f"crop={width}:{height}:0:0"]
    command += ["-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    return command


def _encode_command(
    ffmpeg: str, media: ToolMedia, width: int, height: int, out_path: Path, crf: int,
) -> list[str]:
    suffix = out_path.suffix.lower()
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-r", _rate(media.fps), "-i", "pipe:0",
        "-i", str(media.path),
        "-map", "0:v:0", "-map", "1:a:0?",
        "-vf", f"scale=out_color_matrix={YUV_MATRIX}:out_range=tv,setsar=1",
        "-colorspace", YUV_MATRIX, "-color_primaries", YUV_MATRIX, "-color_trc", YUV_MATRIX,
        "-color_range", "tv",
    ]
    command += audio_codec_args(suffix, suffix != media.path.suffix.lower())
    command += video_encoder_args(suffix, crf)
    command += [str(out_path)]
    return command


def _tail(handle: IO[bytes]) -> str:
    handle.seek(0)
    return handle.read()[-STDERR_TAIL_CHARS:].decode("utf-8", errors="replace")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def _spawn(command: list[str], **streams) -> subprocess.Popen:
    try:
        return subprocess.Popen(command, **streams)
    except OSError as exc:
        raise FfmpegUnavailable("ffmpeg could not be started on this server") from exc


def stream_video_frames(
    media: ToolMedia,
    ctx: ToolContext,
    transform: FrameTransform,
    *,
    crf: int = CRF_BY_QUALITY["high"],
) -> ToolMedia:
    if not media.width or not media.height:
        raise ToolFailed("Could not read the video's size")
    width, height = media.width - media.width % 2, media.height - media.height % 2
    if width < 2 or height < 2:
        raise ToolFailed("The video is too small to edit")
    frame_bytes = width * height * CHANNELS
    fps = Fraction(_rate(media.fps))
    expected = max(1, round(media.duration * float(fps))) if media.duration else None

    ffmpeg = ctx.runner.binary()
    out_path = ctx.new_path(video_output_suffix(media), stem=media.path.stem[:40] or "out")
    decode = _decode_command(ffmpeg, media, width, height)
    encode = _encode_command(ffmpeg, media, width, height, out_path, crf)
    ctx.runner.commands.extend([decode, encode])

    decoder = encoder = None
    with tempfile.TemporaryFile() as decode_errors, tempfile.TemporaryFile() as encode_errors:
        try:
            decoder = _spawn(decode, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=decode_errors)
            encoder = _spawn(encode, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encode_errors)
            count = 0
            while True:
                if ctx.is_cancelled():
                    raise ToolCancelled()
                raw = decoder.stdout.read(frame_bytes)
                if not raw:
                    break
                if len(raw) < frame_bytes:
                    raise ToolFailed("The video ended in the middle of a frame")
                frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, CHANNELS)
                result = np.ascontiguousarray(transform(frame), dtype=np.uint8)
                if result.shape != frame.shape:
                    raise ToolFailed("The effect changed the size of a frame")
                try:
                    encoder.stdin.write(result.data)
                except (BrokenPipeError, OSError):
                    logger.error("ffmpeg encoder stopped early: %s", _tail(encode_errors))
                    raise ToolFailed("Failed to encode the media") from None
                count += 1
                if expected:
                    ctx.progress(min(CODEC_PROGRESS_CAP, count / expected))
            encoder.stdin.close()
            decoder.stdout.close()
            decoder.wait()
            encoder.wait()
            if decoder.returncode != 0 or count == 0:
                logger.error("ffmpeg decoder exited %s: %s", decoder.returncode, _tail(decode_errors))
                raise ToolFailed("Could not read the video's frames")
            if encoder.returncode != 0:
                logger.error("ffmpeg encoder exited %s: %s", encoder.returncode, _tail(encode_errors))
                raise ToolFailed("Failed to encode the media")
        except BaseException:
            for process in (decoder, encoder):
                if process is not None:
                    _stop(process)
                    for stream in (process.stdin, process.stdout):
                        if stream is not None and not stream.closed:
                            try:
                                stream.close()
                            except OSError:
                                pass
            out_path.unlink(missing_ok=True)
            raise

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise ToolFailed("The encoder produced an empty file")
    return ctx.probe(out_path, media.kind)
