from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
from contextlib import ExitStack
from fractions import Fraction
from pathlib import Path
from typing import IO, Callable, Iterator, Optional

import numpy as np

from src.platform.media_tools.fastpath import (
    COLOR_TAGS,
    CRF_BY_QUALITY,
    SQUARE_PIXELS,
    MergedPlan,
    StageGroup,
    audio_map_args,
    output_filters,
    reencodes_audio,
    seek_args,
    video_encoder_args,
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
BATCH_FRAMES = 8
RGB_PIPE = "format=rgb24"


def _rate(fps: float | None) -> str:
    ratio = Fraction(fps if fps and fps > 0 else DEFAULT_FPS).limit_denominator(MAX_FPS_DENOMINATOR)
    return f"{ratio.numerator}/{ratio.denominator}"


def _head(ffmpeg: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error"]


def _pipe_input(width: int, height: int, rate: str) -> list[str]:
    return ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", rate, "-i", "pipe:0"]


def _pipe_output(filters: list[str]) -> list[str]:
    return ["-vf", ",".join([*filters, RGB_PIPE]), "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]


def decode_command(ffmpeg: str, media: ToolMedia, plan: MergedPlan) -> list[str]:
    return [
        *_head(ffmpeg), *seek_args(plan), "-i", str(media.path), "-map", "0:v:0", "-an", "-sn",
        *_pipe_output(plan.segments()[0]),
    ]


def middle_command(ffmpeg: str, group: StageGroup, filters: list[str], rate: str) -> list[str]:
    return [*_head(ffmpeg), *_pipe_input(group.width, group.height, rate), *_pipe_output(filters)]


def encode_command(
    ffmpeg: str, media: ToolMedia, plan: MergedPlan, group: StageGroup, rate: str, out_path: Path
) -> list[str]:
    suffix = out_path.suffix.lower()
    filters = output_filters(plan, plan.segments()[-1])
    if SQUARE_PIXELS not in filters:
        filters.append(SQUARE_PIXELS)
    command = [
        *_head(ffmpeg), "-y", *_pipe_input(group.width, group.height, rate),
        *seek_args(plan), "-i", str(media.path), "-map", "0:v:0",
        *audio_map_args(media, plan, "1:a:0?", suffix, reencodes_audio(media, plan, suffix)),
        "-vf", ",".join(filters), *COLOR_TAGS,
    ]
    if plan.frames is not None:
        command += ["-fps_mode", "passthrough", "-frames:v", str(plan.frames)]
    crf = plan.crf if plan.crf is not None else CRF_BY_QUALITY["high"]
    return command + video_encoder_args(suffix, crf) + [str(out_path)]


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
    for stream in (process.stdin, process.stdout):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass


def _spawn(command: list[str], **streams) -> subprocess.Popen:
    try:
        return subprocess.Popen(command, **streams)
    except OSError as exc:
        raise FfmpegUnavailable("ffmpeg could not be started on this server") from exc


def _read_frames(stream: IO[bytes], width: int, height: int) -> Iterator[np.ndarray]:
    frame_bytes = width * height * CHANNELS
    while True:
        raw = stream.read(frame_bytes)
        if not raw:
            return
        if len(raw) < frame_bytes:
            raise ToolFailed("The video ended in the middle of a frame")
        yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width, CHANNELS)


def _run_group(
    frames: Iterator[np.ndarray], group: StageGroup, fps: float, cancelled: Callable[[], bool]
) -> Iterator[np.ndarray]:
    index = 0
    pending: list[np.ndarray] = []

    def flush() -> np.ndarray:
        nonlocal index
        stack = np.stack(pending)
        indices = tuple(range(index, index + len(pending)))
        for stage in group.stages:
            stack = stage.apply(stack, indices, fps, group.duration)
        index += len(pending)
        pending.clear()
        return stack

    for frame in frames:
        if cancelled():
            raise ToolCancelled()
        pending.append(frame)
        if len(pending) == BATCH_FRAMES:
            yield from flush()
    if pending:
        yield from flush()


class _Feeder(threading.Thread):
    def __init__(self, frames: Iterator[np.ndarray], sink: IO[bytes], shape: tuple[int, int, int]):
        super().__init__(daemon=True)
        self.frames = frames
        self.sink = sink
        self.shape = shape
        self.error: Optional[BaseException] = None

    def run(self) -> None:
        try:
            for frame in self.frames:
                if frame.shape != self.shape:
                    raise ToolFailed("The effect changed the size of a frame")
                try:
                    self.sink.write(np.ascontiguousarray(frame, dtype=np.uint8).data)
                except (BrokenPipeError, ValueError, OSError):
                    self.error = ToolFailed("Failed to apply the effects")
                    return
        except BaseException as exc:
            self.error = exc
        finally:
            try:
                self.sink.close()
            except OSError:
                pass


def stream_segments(media: ToolMedia, plan: MergedPlan, ctx: ToolContext, out_path: Path) -> ToolMedia:
    if not media.width or not media.height or any(not g.width or not g.height for g in plan.groups):
        raise ToolFailed("Could not read the video's size")
    ffmpeg = ctx.runner.binary()
    rate = _rate(media.fps)
    fps = float(Fraction(rate))
    segments = plan.segments()
    groups = plan.groups
    last = groups[-1]
    expected = max(1, round(last.duration * fps)) if last.duration else None

    commands = [decode_command(ffmpeg, media, plan)]
    commands += [middle_command(ffmpeg, group, segments[index + 1], rate) for index, group in enumerate(groups[:-1])]
    commands.append(encode_command(ffmpeg, media, plan, last, rate, out_path))
    ctx.runner.commands.extend(commands)

    processes: list[subprocess.Popen] = []
    feeders: list[_Feeder] = []
    with ExitStack() as stack:
        errors = [stack.enter_context(tempfile.TemporaryFile()) for _ in commands]
        try:
            decoder = _spawn(commands[0], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors[0])
            processes.append(decoder)
            frames = _read_frames(decoder.stdout, groups[0].width, groups[0].height)
            for index, group in enumerate(groups):
                frames = _run_group(frames, group, fps, ctx.is_cancelled)
                if group is last:
                    break
                middle = _spawn(
                    commands[index + 1], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors[index + 1],
                )
                processes.append(middle)
                feeder = _Feeder(frames, middle.stdin, (group.height, group.width, CHANNELS))
                feeders.append(feeder)
                feeder.start()
                following = groups[index + 1]
                frames = _read_frames(middle.stdout, following.width, following.height)
            encoder = _spawn(commands[-1], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors[-1])
            processes.append(encoder)
            shape = (last.height, last.width, CHANNELS)
            count = 0
            for frame in frames:
                if frame.shape != shape:
                    raise ToolFailed("The effect changed the size of a frame")
                try:
                    encoder.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).data)
                except (BrokenPipeError, OSError):
                    logger.error("ffmpeg encoder stopped early: %s", _tail(errors[-1]))
                    raise ToolFailed("Failed to encode the media") from None
                count += 1
                if expected:
                    ctx.progress(min(CODEC_PROGRESS_CAP, count / expected))
            encoder.stdin.close()
            for feeder in feeders:
                feeder.join()
            for process in processes:
                process.wait()
            for feeder in feeders:
                if feeder.error is not None:
                    raise feeder.error
            if decoder.returncode != 0 or count == 0:
                logger.error("ffmpeg decoder exited %s: %s", decoder.returncode, _tail(errors[0]))
                raise ToolFailed("Could not read the video's frames")
            for position, process in enumerate(processes[1:-1], start=1):
                if process.returncode != 0:
                    logger.error("ffmpeg effect segment exited %s: %s", process.returncode, _tail(errors[position]))
                    raise ToolFailed("Failed to apply the effects")
            if encoder.returncode != 0:
                logger.error("ffmpeg encoder exited %s: %s", encoder.returncode, _tail(errors[-1]))
                raise ToolFailed("Failed to encode the media")
        except BaseException:
            for process in processes:
                _stop(process)
            for feeder in feeders:
                feeder.join(timeout=STOP_GRACE_SECONDS)
            out_path.unlink(missing_ok=True)
            raise

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise ToolFailed("The encoder produced an empty file")
    return ctx.probe(out_path, media.kind)
