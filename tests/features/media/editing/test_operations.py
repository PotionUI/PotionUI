"""Tests for the media editing transform layer.

Everything here runs a real file through the real tool - Pillow for images,
the ffmpeg binary for video and audio - and asserts on the file that came out
by reading it back. A test that asserted on the return value alone would pass
against a function that wrote nothing.

The video and audio cases need `ffmpeg` on PATH; where it is missing they skip
rather than pretend. The image cases and every bounds check run everywhere,
because validation happens before the encoder is ever reached.
"""

import shutil
import subprocess

import pytest
from PIL import Image

from src.features.media.editing.dto import (
    CropOperation,
    FlipOperation,
    ResizeOperation,
    RotateOperation,
    TrimOperation,
)
from src.features.media.editing import operations
from src.features.media.editing.operations import (
    MAX_SPLIT_PARTS,
    InvalidEditError,
    MediaEditFailedError,
    apply_audio_operations,
    apply_image_operations,
    apply_video_operations,
    extract_video_frame,
    split_audio,
    validate_operations,
)
from tests.fixtures.audio_fixtures import build_minimal_wav

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")

VIDEO_SECONDS = 2
VIDEO_FPS = 10


def make_image(path, width=200, height=100, marker=(255, 0, 0)):
    """A real image whose top-left pixel is `marker` and the rest black."""
    image = Image.new("RGB", (width, height), (0, 0, 0))
    image.putpixel((0, 0), marker)
    image.save(path)
    return path


def make_video(path, seconds=VIDEO_SECONDS, width=64, height=48, fps=VIDEO_FPS):
    """A real, decodable clip of known length, built by ffmpeg itself."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-nostdin",
            "-f", "lavfi",
            "-i", f"testsrc=duration={seconds}:size={width}x{height}:rate={fps}",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return path


def make_wav(path, seconds=1.0):
    path.write_bytes(build_minimal_wav(duration_seconds=seconds))
    return path


# ========== Images ==========


def test_crop_writes_an_image_of_the_cropped_size(tmp_path):
    source = make_image(tmp_path / "in.png", 200, 100)
    dest = tmp_path / "out.png"

    metadata = apply_image_operations(
        source, dest, [CropOperation(type="crop", x=10, y=20, width=50, height=30)]
    )

    with Image.open(dest) as written:
        assert written.size == (50, 30)
    assert (metadata.width, metadata.height) == (50, 30)


def test_resize_with_only_a_width_keeps_the_aspect_ratio(tmp_path):
    source = make_image(tmp_path / "in.png", 200, 100)
    dest = tmp_path / "out.png"

    apply_image_operations(source, dest, [ResizeOperation(type="resize", width=50)])

    with Image.open(dest) as written:
        assert written.size == (50, 25)


def test_rotate_turns_clockwise(tmp_path):
    source = make_image(tmp_path / "in.png", 200, 100)
    dest = tmp_path / "out.png"

    apply_image_operations(source, dest, [RotateOperation(type="rotate", degrees=90)])

    with Image.open(dest) as written:
        assert written.size == (100, 200)
        # The marker started top-left; a clockwise quarter turn puts it top-right.
        assert written.getpixel((written.width - 1, 0)) == (255, 0, 0)


def test_flip_mirrors_horizontally(tmp_path):
    source = make_image(tmp_path / "in.png", 200, 100)
    dest = tmp_path / "out.png"

    apply_image_operations(source, dest, [FlipOperation(type="flip", axis="horizontal")])

    with Image.open(dest) as written:
        assert written.getpixel((written.width - 1, 0)) == (255, 0, 0)


def test_operations_apply_in_order(tmp_path):
    source = make_image(tmp_path / "in.png", 200, 100)
    dest = tmp_path / "out.png"

    apply_image_operations(source, dest, [
        CropOperation(type="crop", x=0, y=0, width=100, height=100),
        ResizeOperation(type="resize", width=25, height=25),
    ])

    with Image.open(dest) as written:
        assert written.size == (25, 25)


def test_a_crop_is_checked_against_what_the_previous_operation_left(tmp_path):
    """The second crop fits the original but not the 100x100 the first produced."""
    source = make_image(tmp_path / "in.png", 200, 100)
    dest = tmp_path / "out.png"

    with pytest.raises(InvalidEditError):
        apply_image_operations(source, dest, [
            CropOperation(type="crop", x=0, y=0, width=100, height=100),
            CropOperation(type="crop", x=120, y=0, width=50, height=50),
        ])

    assert not dest.exists()


def test_a_crop_past_the_edge_is_refused_and_writes_nothing(tmp_path):
    source = make_image(tmp_path / "in.png", 200, 100)
    dest = tmp_path / "out.png"

    with pytest.raises(InvalidEditError):
        apply_image_operations(
            source, dest, [CropOperation(type="crop", x=150, y=0, width=100, height=50)]
        )

    assert not dest.exists()


def test_a_negative_crop_origin_is_refused(tmp_path):
    source = make_image(tmp_path / "in.png")

    with pytest.raises(InvalidEditError):
        apply_image_operations(
            source, tmp_path / "out.png",
            [CropOperation(type="crop", x=-5, y=0, width=10, height=10)],
        )


def test_a_zero_size_crop_is_refused(tmp_path):
    source = make_image(tmp_path / "in.png")

    with pytest.raises(InvalidEditError):
        apply_image_operations(
            source, tmp_path / "out.png",
            [CropOperation(type="crop", x=0, y=0, width=0, height=10)],
        )


def test_a_negative_resize_is_refused(tmp_path):
    source = make_image(tmp_path / "in.png")

    with pytest.raises(InvalidEditError):
        apply_image_operations(
            source, tmp_path / "out.png", [ResizeOperation(type="resize", width=-10)]
        )


def test_a_resize_past_the_dimension_limit_is_refused(tmp_path):
    source = make_image(tmp_path / "in.png")

    with pytest.raises(InvalidEditError):
        apply_image_operations(
            source, tmp_path / "out.png", [ResizeOperation(type="resize", width=99999)]
        )


def test_a_resize_with_neither_side_is_refused(tmp_path):
    source = make_image(tmp_path / "in.png")

    with pytest.raises(InvalidEditError):
        apply_image_operations(
            source, tmp_path / "out.png", [ResizeOperation(type="resize")]
        )


def test_an_empty_operation_list_is_refused(tmp_path):
    source = make_image(tmp_path / "in.png")

    with pytest.raises(InvalidEditError):
        apply_image_operations(source, tmp_path / "out.png", [])


def test_too_many_operations_are_refused(tmp_path):
    source = make_image(tmp_path / "in.png")
    flip = FlipOperation(type="flip", axis="horizontal")

    with pytest.raises(InvalidEditError):
        apply_image_operations(source, tmp_path / "out.png", [flip] * 9)


def test_an_rgba_source_can_be_written_as_jpeg(tmp_path):
    source = tmp_path / "in.png"
    Image.new("RGBA", (40, 30), (255, 0, 0, 128)).save(source)
    dest = tmp_path / "out.jpg"

    apply_image_operations(source, dest, [ResizeOperation(type="resize", width=20)])

    with Image.open(dest) as written:
        assert written.size == (20, 15)
        assert written.format == "JPEG"


def test_a_crop_is_taken_in_the_orientation_the_browser_showed(tmp_path):
    """The stored frame is 200x100 but EXIF orientation 6 displays it as
    100x200, which is the geometry the user drew their rectangle on. Reading
    the file without honouring that makes their crop land somewhere else - or,
    as here, fall outside the image entirely."""
    source = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # Rotate 90 clockwise for display.
    Image.new("RGB", (200, 100), (0, 0, 0)).save(source, exif=exif)
    dest = tmp_path / "out.jpg"

    metadata = apply_image_operations(
        source, dest, [CropOperation(type="crop", x=0, y=0, width=100, height=200)]
    )

    assert (metadata.width, metadata.height) == (100, 200)
    with Image.open(dest) as written:
        assert written.size == (100, 200)


def test_a_file_that_is_not_an_image_is_refused(tmp_path):
    source = tmp_path / "in.png"
    source.write_bytes(b"not an image at all")
    dest = tmp_path / "out.png"

    with pytest.raises(InvalidEditError):
        apply_image_operations(source, dest, [FlipOperation(type="flip", axis="vertical")])

    assert not dest.exists()


# ========== Operation vocabulary per media kind ==========


def test_trim_is_not_an_image_operation():
    with pytest.raises(InvalidEditError):
        validate_operations(
            [TrimOperation(type="trim", start_seconds=0, end_seconds=1)], "image"
        )


def test_crop_is_not_an_audio_operation():
    with pytest.raises(InvalidEditError):
        validate_operations(
            [CropOperation(type="crop", x=0, y=0, width=1, height=1)], "audio"
        )


def test_a_mesh_has_no_editable_operations():
    with pytest.raises(InvalidEditError, match="model"):
        validate_operations([FlipOperation(type="flip", axis="vertical")], "model")


def test_only_one_trim_at_a_time():
    trim = TrimOperation(type="trim", start_seconds=0, end_seconds=1)

    with pytest.raises(InvalidEditError):
        validate_operations([trim, trim], "video")


# ========== Video ==========


@needs_ffmpeg
def test_trimming_a_video_produces_a_clip_of_the_trimmed_length(tmp_path):
    source = make_video(tmp_path / "in.mp4")
    dest = tmp_path / "out.mp4"

    metadata = apply_video_operations(
        source, dest, [TrimOperation(type="trim", start_seconds=0.5, end_seconds=1.5)]
    )

    assert dest.stat().st_size > 0
    assert metadata.duration_seconds == pytest.approx(1.0, abs=0.2)


@needs_ffmpeg
def test_cropping_and_resizing_a_video_changes_its_real_dimensions(tmp_path):
    source = make_video(tmp_path / "in.mp4", width=64, height=48)
    dest = tmp_path / "out.mp4"

    metadata = apply_video_operations(source, dest, [
        CropOperation(type="crop", x=0, y=0, width=32, height=24),
        ResizeOperation(type="resize", width=16, height=12),
    ])

    assert (metadata.width, metadata.height) == (16, 12)


@needs_ffmpeg
def test_rotating_a_video_swaps_its_real_dimensions(tmp_path):
    source = make_video(tmp_path / "in.mp4", width=64, height=48)
    dest = tmp_path / "out.mp4"

    metadata = apply_video_operations(
        source, dest, [RotateOperation(type="rotate", degrees=90)]
    )

    assert (metadata.width, metadata.height) == (48, 64)


@needs_ffmpeg
def test_an_odd_target_size_is_rounded_to_an_even_frame(tmp_path):
    """yuv420p cannot encode an odd dimension; the encoder refuses the file
    outright rather than losing the pixel, so the pixel is given up here."""
    source = make_video(tmp_path / "in.mp4", width=64, height=48)
    dest = tmp_path / "out.mp4"

    metadata = apply_video_operations(
        source, dest, [ResizeOperation(type="resize", width=33, height=25)]
    )

    assert (metadata.width, metadata.height) == (32, 24)


@needs_ffmpeg
def test_an_odd_crop_is_rounded_to_an_even_frame(tmp_path):
    source = make_video(tmp_path / "in.mp4", width=64, height=48)
    dest = tmp_path / "out.mp4"

    metadata = apply_video_operations(
        source, dest, [CropOperation(type="crop", x=1, y=1, width=33, height=25)]
    )

    assert (metadata.width, metadata.height) == (32, 24)


@needs_ffmpeg
def test_an_inverted_trim_is_refused_before_the_encoder_runs(tmp_path):
    source = make_video(tmp_path / "in.mp4")
    dest = tmp_path / "out.mp4"

    with pytest.raises(InvalidEditError):
        apply_video_operations(
            source, dest, [TrimOperation(type="trim", start_seconds=1.5, end_seconds=0.5)]
        )

    assert not dest.exists()


@needs_ffmpeg
def test_a_trim_past_the_end_is_refused(tmp_path):
    source = make_video(tmp_path / "in.mp4", seconds=2)
    dest = tmp_path / "out.mp4"

    with pytest.raises(InvalidEditError):
        apply_video_operations(
            source, dest, [TrimOperation(type="trim", start_seconds=0, end_seconds=30)]
        )

    assert not dest.exists()


@needs_ffmpeg
def test_a_trim_starting_past_the_end_is_refused(tmp_path):
    source = make_video(tmp_path / "in.mp4", seconds=2)

    with pytest.raises(InvalidEditError):
        apply_video_operations(
            source, tmp_path / "out.mp4",
            [TrimOperation(type="trim", start_seconds=9, end_seconds=10)],
        )


@needs_ffmpeg
def test_an_unreadable_source_surfaces_as_an_encoder_failure(tmp_path):
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"\x00" * 4096)
    dest = tmp_path / "out.mp4"

    with pytest.raises((MediaEditFailedError, InvalidEditError)):
        apply_video_operations(
            source, dest, [ResizeOperation(type="resize", width=16, height=16)]
        )

    assert not dest.exists()


def test_a_failed_encode_leaves_no_partial_file(tmp_path, monkeypatch):
    """The process is stubbed, but the file and the assertion are real: a
    half-written output that outlives its failed encode is servable to anyone
    who guesses its name."""
    dest = tmp_path / "out.mp4"

    def _fail_after_writing(*_args, **_kwargs):
        dest.write_bytes(b"half an mp4")
        return subprocess.CompletedProcess([], returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(operations.subprocess, "run", _fail_after_writing)

    with pytest.raises(MediaEditFailedError):
        operations._run_encoder(["ffmpeg"], dest)

    assert not dest.exists()


def test_an_empty_encode_result_is_refused_and_removed(tmp_path, monkeypatch):
    dest = tmp_path / "out.mp4"

    def _succeed_with_nothing(*_args, **_kwargs):
        dest.write_bytes(b"")
        return subprocess.CompletedProcess([], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(operations.subprocess, "run", _succeed_with_nothing)

    with pytest.raises(MediaEditFailedError):
        operations._run_encoder(["ffmpeg"], dest)

    assert not dest.exists()


def test_a_missing_ffmpeg_is_reported_not_swallowed(tmp_path, monkeypatch):
    def _not_installed(*_args, **_kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(operations.subprocess, "run", _not_installed)

    with pytest.raises(MediaEditFailedError, match="ffmpeg"):
        operations._run_encoder(["ffmpeg"], tmp_path / "out.mp4")


@needs_ffmpeg
def test_extracting_a_frame_writes_a_real_image(tmp_path):
    source = make_video(tmp_path / "in.mp4", width=64, height=48)
    dest = tmp_path / "frame.png"

    metadata = extract_video_frame(source, dest, 1.0)

    with Image.open(dest) as frame:
        assert frame.size == (64, 48)
    assert (metadata.width, metadata.height) == (64, 48)


@needs_ffmpeg
def test_a_frame_past_the_end_is_refused(tmp_path):
    source = make_video(tmp_path / "in.mp4", seconds=2)
    dest = tmp_path / "frame.png"

    with pytest.raises(InvalidEditError):
        extract_video_frame(source, dest, 30.0)

    assert not dest.exists()


@needs_ffmpeg
def test_a_negative_frame_time_is_refused(tmp_path):
    source = make_video(tmp_path / "in.mp4")

    with pytest.raises(InvalidEditError):
        extract_video_frame(source, tmp_path / "frame.png", -1.0)


# ========== Audio ==========


@needs_ffmpeg
def test_trimming_audio_produces_a_clip_of_the_trimmed_length(tmp_path):
    source = make_wav(tmp_path / "in.wav", seconds=2.0)
    dest = tmp_path / "out.wav"

    metadata = apply_audio_operations(
        source, dest, [TrimOperation(type="trim", start_seconds=0.5, end_seconds=1.0)]
    )

    assert dest.stat().st_size > 0
    assert metadata.duration_seconds == pytest.approx(0.5, abs=0.05)


@needs_ffmpeg
def test_an_audio_trim_past_the_end_is_refused(tmp_path):
    source = make_wav(tmp_path / "in.wav", seconds=1.0)
    dest = tmp_path / "out.wav"

    with pytest.raises(InvalidEditError):
        apply_audio_operations(
            source, dest, [TrimOperation(type="trim", start_seconds=0, end_seconds=5)]
        )

    assert not dest.exists()


# ========== Splitting ==========


@needs_ffmpeg
def test_splitting_an_exact_multiple_produces_one_part_per_chunk(tmp_path):
    source = make_wav(tmp_path / "in.wav", seconds=60.0)
    dest_dir = tmp_path / "parts"
    dest_dir.mkdir()

    parts = split_audio(source, dest_dir, ".wav", 10.0)

    assert len(parts) == 6
    for path, metadata in parts:
        assert path.exists()
        assert metadata.duration_seconds == pytest.approx(10.0, abs=0.25)


@needs_ffmpeg
def test_a_short_remainder_is_kept_as_a_final_part(tmp_path):
    source = make_wav(tmp_path / "in.wav", seconds=65.0)
    dest_dir = tmp_path / "parts"
    dest_dir.mkdir()

    parts = split_audio(source, dest_dir, ".wav", 10.0)

    assert len(parts) == 7
    last_duration = parts[-1][1].duration_seconds
    assert last_duration == pytest.approx(5.0, abs=0.25)


@needs_ffmpeg
def test_a_part_length_not_shorter_than_the_media_is_refused(tmp_path):
    source = make_wav(tmp_path / "in.wav", seconds=5.0)
    dest_dir = tmp_path / "parts"
    dest_dir.mkdir()

    with pytest.raises(InvalidEditError):
        split_audio(source, dest_dir, ".wav", 10.0)

    assert list(dest_dir.iterdir()) == []


@needs_ffmpeg
def test_a_part_count_over_the_cap_is_refused(tmp_path):
    source = make_wav(tmp_path / "in.wav", seconds=5.0)
    dest_dir = tmp_path / "parts"
    dest_dir.mkdir()

    part_seconds = 5.0 / (MAX_SPLIT_PARTS + 1)

    with pytest.raises(InvalidEditError):
        split_audio(source, dest_dir, ".wav", part_seconds)

    assert list(dest_dir.iterdir()) == []


def test_a_non_positive_part_length_is_refused(tmp_path):
    source = tmp_path / "in.wav"
    dest_dir = tmp_path / "parts"
    dest_dir.mkdir()

    with pytest.raises(InvalidEditError):
        split_audio(source, dest_dir, ".wav", 0.0)


def test_a_non_finite_part_length_is_refused(tmp_path):
    source = tmp_path / "in.wav"
    dest_dir = tmp_path / "parts"
    dest_dir.mkdir()

    with pytest.raises(InvalidEditError):
        split_audio(source, dest_dir, ".wav", float("nan"))
