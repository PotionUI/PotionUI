"""
The transform layer of media editing: bytes in, bytes out.

Nothing here knows about the database, the request, or who is asking. Each
entry point takes a source file, a destination that does not exist yet, and an
ordered list of operations; it either writes a complete file and reports the
resulting metadata, or raises and leaves nothing behind.

Images go through Pillow (already a dependency, and the tool the resize and
thumbnail paths use). Video and audio go through the ffmpeg CLI, which
`src.features.generation.media_probe` already requires - a second encoding
library would be a new dependency for work ffmpeg does natively, and it is the
only tool here that can trim a container without discarding its audio track.

Geometry is validated against the media as it actually is at each step, never
against what the client claims or what the `uploads` row remembers: an
operation's bounds are checked against the dimensions its predecessors left
behind, so `crop` after `resize` is checked against the resized frame.
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError

from src.features.generation import media_probe
from src.features.media.editing.dto import (
    CropOperation,
    EditOperation,
    FlipOperation,
    ResizeOperation,
    RotateOperation,
    TrimOperation,
)

logger = logging.getLogger(__name__)

MAX_IMAGE_DIMENSION = 8192
MAX_VIDEO_DIMENSION = 4096
MAX_OPERATIONS = 8

# A trim of a long clip re-encodes the whole selection. Generous, because the
# alternative to waiting is a truncated file; bounded, because a wedged encoder
# must not hold a worker thread forever.
FFMPEG_TIMEOUT_SECONDS = 900

# A part length near-zero on a long file would otherwise mint thousands of
# rows from one request.
MAX_SPLIT_PARTS = 200

_IMAGE_OPERATIONS = ("crop", "resize", "rotate", "flip")
_VIDEO_OPERATIONS = ("trim", "crop", "resize", "rotate", "flip")
_AUDIO_OPERATIONS = ("trim",)

# Pillow's ROTATE_* transposes turn counter-clockwise; the API's `degrees` are
# clockwise, which is what a user pressing a rotate-right button means.
_CLOCKWISE_TRANSPOSE = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}

# ffmpeg's transpose=1 is clockwise, transpose=2 counter-clockwise.
_CLOCKWISE_TRANSPOSE_FILTER = {
    90: ["transpose=1"],
    180: ["transpose=1", "transpose=1"],
    270: ["transpose=2"],
}

_JPEG_SUFFIXES = (".jpg", ".jpeg")


class InvalidEditError(ValueError):
    """The edit cannot be applied to this media - bad geometry, wrong kind, empty.

    A subclass of ValueError so a caller that only cares about "the request was
    wrong" can catch either; controllers catch this one first, because a plain
    ValueError here means "no such resource" and answers 404.
    """


class MediaEditFailedError(RuntimeError):
    """The encoder failed, timed out, or is not installed."""


@dataclass
class EditedMediaMetadata:
    """What the written file turned out to be."""

    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None


# ========== Images ==========


def apply_image_operations(
    source: Path,
    dest: Path,
    operations: Sequence[EditOperation],
) -> EditedMediaMetadata:
    """Apply `operations` to an image, writing the result to `dest`."""
    validate_operations(operations, "image")

    try:
        with Image.open(source) as opened:
            # Browsers render an image through its EXIF orientation, so the crop
            # rectangle a user drew is in transposed coordinates. Baking the
            # orientation in first is what makes their rectangle land where they
            # drew it.
            image = ImageOps.exif_transpose(opened) or opened

            for operation in operations:
                image = _apply_image_operation(image, operation)

            _save_image(image, dest)
            width, height = image.size
    except (UnidentifiedImageError, OSError) as e:
        _discard(dest)
        raise InvalidEditError(f"This image cannot be edited: {e}")
    except Exception:
        _discard(dest)
        raise

    return EditedMediaMetadata(width=width, height=height)


def _apply_image_operation(image: Image.Image, operation: EditOperation) -> Image.Image:
    width, height = image.size

    if isinstance(operation, CropOperation):
        box = _validated_crop(operation, width, height)
        return image.crop(box)

    if isinstance(operation, ResizeOperation):
        target = _validated_resize(operation, width, height, MAX_IMAGE_DIMENSION)
        return image.resize(target, Image.Resampling.LANCZOS)

    if isinstance(operation, RotateOperation):
        return image.transpose(_CLOCKWISE_TRANSPOSE[operation.degrees])

    if isinstance(operation, FlipOperation):
        transpose = (
            Image.Transpose.FLIP_LEFT_RIGHT
            if operation.axis == "horizontal"
            else Image.Transpose.FLIP_TOP_BOTTOM
        )
        return image.transpose(transpose)

    raise InvalidEditError(f"Operation '{operation.type}' cannot be applied to an image")


def _save_image(image: Image.Image, dest: Path) -> None:
    """Write `image` to `dest`, in the format `dest`'s extension names."""
    if dest.suffix.lower() in _JPEG_SUFFIXES and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.save(dest)


# ========== Video ==========


def apply_video_operations(
    source: Path,
    dest: Path,
    operations: Sequence[EditOperation],
) -> EditedMediaMetadata:
    """Apply `operations` to a video, writing the result to `dest`."""
    validate_operations(operations, "video")

    width, height = media_probe.get_video_dimensions(str(source))
    if not width or not height:
        raise MediaEditFailedError("Could not read the video's dimensions")

    trim = _single_trim(operations)
    seek_args: List[str] = []
    if trim:
        duration, _ = media_probe.get_video_duration_fps(str(source))
        start, length = _validated_trim(trim, duration)
        seek_args = ["-ss", f"{start:.6f}", "-t", f"{length:.6f}"]

    filters = _video_filter_chain(operations, width, height)

    command = [
        "ffmpeg", "-y", "-nostdin",
        *seek_args,
        "-i", str(source),
    ]
    if filters:
        command += ["-vf", ",".join(filters)]
    command += ["-pix_fmt", "yuv420p", str(dest)]

    _run_encoder(command, dest)

    out_width, out_height = media_probe.get_video_dimensions(str(dest))
    out_duration, out_fps = media_probe.get_video_duration_fps(str(dest))
    return EditedMediaMetadata(
        width=out_width,
        height=out_height,
        duration_seconds=out_duration,
        fps=out_fps,
    )


def _video_filter_chain(
    operations: Sequence[EditOperation],
    width: int,
    height: int,
) -> List[str]:
    """Build the `-vf` chain, validating each step against the frame before it.

    Every dimension the chain produces is forced even. yuv420p subsamples chroma
    by two, and an odd width or height makes the encoder either fail outright or
    silently pad - both worse than losing a pixel.
    """
    filters: List[str] = []

    for operation in operations:
        if isinstance(operation, TrimOperation):
            continue  # Applied as an input seek, before any filter runs.

        if isinstance(operation, CropOperation):
            left, top, right, bottom = _validated_crop(operation, width, height)
            crop_width = _even(right - left)
            crop_height = _even(bottom - top)
            if crop_width <= 0 or crop_height <= 0:
                raise InvalidEditError("The crop rectangle is too small")
            filters.append(f"crop={crop_width}:{crop_height}:{_even(left)}:{_even(top)}")
            width, height = crop_width, crop_height

        elif isinstance(operation, ResizeOperation):
            target_width, target_height = _validated_resize(
                operation, width, height, MAX_VIDEO_DIMENSION
            )
            target_width, target_height = _even(target_width), _even(target_height)
            if target_width <= 0 or target_height <= 0:
                raise InvalidEditError("The target size is too small")
            filters.append(f"scale={target_width}:{target_height}")
            width, height = target_width, target_height

        elif isinstance(operation, RotateOperation):
            filters.extend(_CLOCKWISE_TRANSPOSE_FILTER[operation.degrees])
            if operation.degrees in (90, 270):
                width, height = height, width

        elif isinstance(operation, FlipOperation):
            filters.append("hflip" if operation.axis == "horizontal" else "vflip")

        else:
            raise InvalidEditError(
                f"Operation '{operation.type}' cannot be applied to a video"
            )

    return filters


def extract_video_frame(source: Path, dest: Path, time_seconds: float) -> EditedMediaMetadata:
    """Write the video frame at `time_seconds` to `dest` as a still image."""
    duration, _ = media_probe.get_video_duration_fps(str(source))
    if duration is None:
        raise MediaEditFailedError("Could not read the video's duration")

    if time_seconds < 0:
        raise InvalidEditError("The frame time cannot be negative")
    if time_seconds >= duration:
        raise InvalidEditError(
            f"The frame time ({time_seconds:g}s) is past the end of the video ({duration:.3f}s)"
        )

    command = [
        "ffmpeg", "-y", "-nostdin",
        "-ss", f"{time_seconds:.6f}",
        "-i", str(source),
        "-frames:v", "1",
        str(dest),
    ]
    _run_encoder(command, dest)

    with Image.open(dest) as frame:
        width, height = frame.size
    return EditedMediaMetadata(width=width, height=height)


# ========== Audio ==========


def apply_audio_operations(
    source: Path,
    dest: Path,
    operations: Sequence[EditOperation],
) -> EditedMediaMetadata:
    """Apply `operations` to an audio file, writing the result to `dest`."""
    validate_operations(operations, "audio")

    trim = _single_trim(operations)
    if not trim:
        raise InvalidEditError("Audio can only be trimmed")

    duration = media_probe.get_audio_duration_seconds(str(source))
    start, length = _validated_trim(trim, duration)

    command = [
        "ffmpeg", "-y", "-nostdin",
        "-ss", f"{start:.6f}",
        "-i", str(source),
        "-t", f"{length:.6f}",
        "-vn",
        str(dest),
    ]
    _run_encoder(command, dest)

    return EditedMediaMetadata(
        duration_seconds=media_probe.get_audio_duration_seconds(str(dest))
    )


def split_audio(
    source: Path,
    dest_dir: Path,
    suffix: str,
    part_seconds: float,
) -> List[Tuple[Path, EditedMediaMetadata]]:
    """Split an audio file into fixed-length parts, written into `dest_dir`.

    One ffmpeg pass through the segment muxer, re-encoding rather than
    `-c copy`: a stream-copied segment cuts on the nearest packet boundary,
    which for most audio codecs is a coarser grain than a part length a user
    typed in seconds. A trailing remainder shorter than `part_seconds` is kept
    as a final short part rather than folded into the one before it.
    """
    if not math.isfinite(part_seconds) or part_seconds <= 0:
        raise InvalidEditError("The part length must be a positive number of seconds")

    duration = media_probe.get_audio_duration_seconds(str(source))
    if duration is None:
        raise MediaEditFailedError("Could not read the media's duration")

    if part_seconds >= duration:
        raise InvalidEditError(
            f"The part length ({part_seconds:g}s) is not shorter than the media "
            f"({duration:.3f}s) - there is nothing to split"
        )

    part_count = math.ceil(duration / part_seconds)
    if part_count > MAX_SPLIT_PARTS:
        raise InvalidEditError(
            f"Splitting into {part_count} parts exceeds the {MAX_SPLIT_PARTS}-part limit"
        )

    pattern = dest_dir / f"part_%03d{suffix}"
    command = [
        "ffmpeg", "-y", "-nostdin",
        "-i", str(source),
        "-f", "segment",
        "-segment_time", f"{part_seconds:.6f}",
        "-reset_timestamps", "1",
        "-vn",
        str(pattern),
    ]
    parts = _run_segment_encoder(command, dest_dir)

    return [
        (part, EditedMediaMetadata(
            duration_seconds=media_probe.get_audio_duration_seconds(str(part))
        ))
        for part in parts
    ]


# ========== Validation ==========


def validate_operations(operations: Sequence[EditOperation], media_type: str) -> None:
    """Reject an operation list that no amount of encoding could satisfy.

    Raises:
        InvalidEditError: If the list is empty, too long, or holds an operation
            this media kind has no meaning for.
    """
    if not operations:
        raise InvalidEditError("No operations were given")
    if len(operations) > MAX_OPERATIONS:
        raise InvalidEditError(f"At most {MAX_OPERATIONS} operations can be applied at once")

    allowed = {
        "image": _IMAGE_OPERATIONS,
        "video": _VIDEO_OPERATIONS,
        "audio": _AUDIO_OPERATIONS,
    }.get(media_type)
    if allowed is None:
        raise InvalidEditError(f"Cannot edit a {media_type} resource")

    for operation in operations:
        if operation.type not in allowed:
            raise InvalidEditError(
                f"Operation '{operation.type}' cannot be applied to {media_type}"
            )

    if len([op for op in operations if isinstance(op, TrimOperation)]) > 1:
        raise InvalidEditError("Only one trim can be applied at a time")


def _single_trim(operations: Sequence[EditOperation]) -> Optional[TrimOperation]:
    for operation in operations:
        if isinstance(operation, TrimOperation):
            return operation
    return None


def _validated_crop(
    operation: CropOperation,
    width: int,
    height: int,
) -> Tuple[int, int, int, int]:
    """The crop box as (left, top, right, bottom), or a refusal."""
    if operation.width <= 0 or operation.height <= 0:
        raise InvalidEditError("The crop size must be positive")
    if operation.x < 0 or operation.y < 0:
        raise InvalidEditError("The crop origin cannot be negative")
    if operation.x + operation.width > width or operation.y + operation.height > height:
        raise InvalidEditError(
            f"The crop rectangle ({operation.x},{operation.y} "
            f"{operation.width}x{operation.height}) does not fit inside {width}x{height}"
        )
    return (
        operation.x,
        operation.y,
        operation.x + operation.width,
        operation.y + operation.height,
    )


def _validated_resize(
    operation: ResizeOperation,
    width: int,
    height: int,
    maximum: int,
) -> Tuple[int, int]:
    """The target size, filling in whichever side the caller left out."""
    if operation.width is None and operation.height is None:
        raise InvalidEditError("A resize needs a width, a height, or both")

    target_width = operation.width
    target_height = operation.height

    if target_width is not None and target_width <= 0:
        raise InvalidEditError("The target width must be positive")
    if target_height is not None and target_height <= 0:
        raise InvalidEditError("The target height must be positive")

    if target_width is None:
        target_width = max(1, round(width * (target_height / height)))
    elif target_height is None:
        target_height = max(1, round(height * (target_width / width)))

    if target_width > maximum or target_height > maximum:
        raise InvalidEditError(
            f"The target size ({target_width}x{target_height}) exceeds the {maximum}px limit"
        )

    return target_width, target_height


def _validated_trim(operation: TrimOperation, duration: Optional[float]) -> Tuple[float, float]:
    """The trim as (start, length), checked against the medium's real duration."""
    if duration is None:
        raise MediaEditFailedError("Could not read the media's duration")

    if operation.start_seconds < 0:
        raise InvalidEditError("The trim cannot start before the beginning")
    if operation.end_seconds <= operation.start_seconds:
        raise InvalidEditError("The trim must end after it starts")
    if operation.start_seconds >= duration:
        raise InvalidEditError(
            f"The trim starts ({operation.start_seconds:g}s) past the end ({duration:.3f}s)"
        )
    # A hair over the probed duration is the rounding of a UI that rendered the
    # same number; a real overshoot is a client that never looked at the media.
    if operation.end_seconds > duration + 0.05:
        raise InvalidEditError(
            f"The trim ends ({operation.end_seconds:g}s) past the end ({duration:.3f}s)"
        )

    end = min(operation.end_seconds, duration)
    return operation.start_seconds, end - operation.start_seconds


def _even(value: int) -> int:
    return value - (value % 2)


# ========== Encoder ==========


def _run_encoder(command: List[str], dest: Path) -> None:
    """Run ffmpeg, leaving no file behind unless it wrote a complete one."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        _discard(dest)
        raise MediaEditFailedError("ffmpeg is not available on this server")
    except subprocess.TimeoutExpired:
        _discard(dest)
        raise MediaEditFailedError("Editing timed out")

    if result.returncode != 0:
        # stderr names absolute server paths, so it is logged and never returned.
        logger.error(
            "ffmpeg exited %s for %s: %s",
            result.returncode, dest.name, (result.stderr or "")[-2000:]
        )
        _discard(dest)
        raise MediaEditFailedError("Failed to encode the edited media")

    if not dest.exists() or dest.stat().st_size == 0:
        _discard(dest)
        raise MediaEditFailedError("The encoder produced an empty file")


def _run_segment_encoder(command: List[str], dest_dir: Path) -> List[Path]:
    """Run ffmpeg's segment muxer, leaving no files behind unless every part wrote."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise MediaEditFailedError("ffmpeg is not available on this server")
    except subprocess.TimeoutExpired:
        raise MediaEditFailedError("Editing timed out")

    produced = sorted(dest_dir.iterdir())

    if result.returncode != 0:
        # stderr names absolute server paths, so it is logged and never returned.
        logger.error(
            "ffmpeg exited %s splitting into %s: %s",
            result.returncode, dest_dir.name, (result.stderr or "")[-2000:]
        )
        for part in produced:
            _discard(part)
        raise MediaEditFailedError("Failed to encode the split media")

    if not produced or any(part.stat().st_size == 0 for part in produced):
        for part in produced:
            _discard(part)
        raise MediaEditFailedError("The encoder produced an incomplete split")

    return produced


def _discard(path: Path) -> None:
    """Remove a partial output, best-effort."""
    try:
        if path.exists():
            path.unlink()
    except OSError as e:
        logger.warning(f"Could not remove partial edit output {path.name}: {e}")
