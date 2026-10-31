import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.outputs import GenerationExecutionError, ProgressGenerationOutput
from src.pipelines.pipes.alpha_video.main import (
    AlphaVideoPipe,
    checkerboard,
    compose_rgba,
    matte_to_alpha,
    over_checkerboard,
)
from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.util import video_stream
from src.platform.util.video_stream import StreamingMp4Writer, require_ffmpeg
from tests.platform.media_tools.media_factory import make_video

FRAMES = 6
FPS = 10
SIZE = (64, 48)


def split_matte(path, size=SIZE, frames=FRAMES, left=0, right=255):
    width, height = size
    shade = np.zeros((height, width, 3), dtype=np.uint8)
    shade[:, : width // 2] = left
    shade[:, width // 2 :] = right
    writer = StreamingMp4Writer(path, width, height, FPS, crf=10)
    for _ in range(frames):
        writer.write(shade)
    writer.close()
    return str(path)


def decode_rgba(path):
    command = [
        require_ffmpeg(), "-v", "error", "-c:v", "libvpx-vp9", "-i", str(path),
        "-f", "rawvideo", "-pix_fmt", "rgba", "-",
    ]
    raw = subprocess.run(command, capture_output=True, check=True).stdout
    probe = cv2.VideoCapture(str(path))
    width = int(probe.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT))
    probe.release()
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width, 4)


def decode_bgr(path):
    capture = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    return frames


def make(**config):
    values = AlphaVideoPipe.get_default_config()
    values.update(config)
    return AlphaVideoPipe(values)


def run(pipe, cancelled=None, **inputs):
    emitted = []
    result = pipe.process(PipeInput(input=inputs), emitted.append, cancelled)
    return result.output["video"], emitted


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("alpha") / "clip.mp4"
    return str(make_video(path, seconds=FRAMES / FPS, size=SIZE, fps=FPS, audio=False))


@pytest.fixture(scope="module")
def matte(tmp_path_factory):
    return split_matte(tmp_path_factory.mktemp("alpha") / "matte.mp4")


class TestPixelMath:
    def test_black_and_white_points_stretch_the_matte(self):
        shade = np.array([[0, 50, 128, 192, 255]], dtype=np.uint8)
        alpha = matte_to_alpha(shade, 5, 1, 0.25, 0.75)
        assert alpha[0, 0] == 0 and alpha[0, 1] == 0 and alpha[0, 4] == 255
        assert 0 < alpha[0, 2] < 255 and alpha[0, 3] > alpha[0, 2]

    def test_a_colour_matte_is_read_by_luminance(self):
        pixel = np.zeros((2, 2, 3), dtype=np.uint8)
        pixel[:] = (255, 255, 255)
        assert matte_to_alpha(pixel, 2, 2, 0.0, 1.0).min() == 255

    def test_a_matte_of_another_size_is_resized_to_the_clip(self):
        shade = np.full((10, 20), 255, dtype=np.uint8)
        assert matte_to_alpha(shade, 8, 6, 0.0, 1.0).shape == (6, 8)

    def test_fully_transparent_pixels_carry_no_colour(self):
        rgb = np.full((1, 2, 3), 200, dtype=np.uint8)
        rgba = compose_rgba(rgb, np.array([[0, 255]], dtype=np.uint8))
        assert rgba[0, 0].tolist() == [0, 0, 0, 0]
        assert rgba[0, 1].tolist() == [200, 200, 200, 255]

    def test_the_checkerboard_alternates_cells(self):
        board = checkerboard(64, 64)
        assert board[0, 0, 0] != board[0, 16, 0]
        assert board[0, 0, 0] == board[16, 16, 0]

    def test_the_preview_blends_by_alpha(self):
        rgb = np.full((2, 2, 3), 100, dtype=np.uint8)
        board = np.full((2, 2, 3), 200, dtype=np.uint8)
        half = over_checkerboard(rgb, np.full((2, 2), 128, dtype=np.uint8), board)
        assert abs(int(half[0, 0, 0]) - 150) <= 1
        assert over_checkerboard(rgb, np.zeros((2, 2), dtype=np.uint8), board)[0, 0, 0] == 200


class TestContract:
    def test_identity_and_ports(self):
        assert AlphaVideoPipe.name == "alpha_video"
        inputs = {spec.name: spec for spec in AlphaVideoPipe.inputs()}
        assert inputs["video"].required and inputs["matte"].required
        assert inputs["video"].io_type == inputs["matte"].io_type == IOType.VIDEO
        (out,) = AlphaVideoPipe.outputs()
        assert (out.name, out.io_type, out.is_array) == ("video", IOType.VIDEO, True)

    def test_a_missing_input_is_an_error(self, clip):
        with pytest.raises(GenerationExecutionError, match="clip and its matte"):
            run(make(), video=[clip], matte=None)

    def test_one_matte_is_needed_per_clip(self, clip, matte):
        with pytest.raises(GenerationExecutionError, match="one matte per clip"):
            run(make(), video=[clip, clip], matte=[matte])

    def test_an_inverted_range_is_rejected(self, clip, matte):
        with pytest.raises(GenerationExecutionError, match="white point"):
            run(make(black_point=0.6, white_point=0.5), video=[clip], matte=[matte])


class TestTransparentWebm:
    def test_the_matte_becomes_the_alpha_channel(self, clip, matte):
        (webm, preview), _ = run(make(), video=[clip], matte=[matte])
        frames = decode_rgba(webm)
        assert frames.shape[0] == FRAMES
        assert frames.shape[1:3] == (SIZE[1], SIZE[0])
        assert frames[:, :, 4:28, 3].max() <= 12
        assert frames[:, :, 36:60, 3].min() >= 243

    def test_the_container_is_webm_with_an_alpha_pixel_format(self, clip, matte):
        (webm, _), _ = run(make(), video=[clip], matte=[matte])
        assert Path(webm).suffix == ".webm"
        header = subprocess.run([require_ffmpeg(), "-i", webm], capture_output=True).stderr.decode(errors="replace")
        assert "Video: vp9" in header
        assert "alpha_mode" in header

    def test_the_frame_rate_and_length_follow_the_clip(self, clip, matte):
        (webm, _), _ = run(make(), video=[clip], matte=[matte])
        capture = cv2.VideoCapture(webm)
        assert capture.get(cv2.CAP_PROP_FPS) == pytest.approx(FPS, abs=0.01)
        capture.release()

    def test_black_and_white_points_clean_the_matte(self, clip, tmp_path):
        grey = split_matte(tmp_path / "grey.mp4", left=60, right=200)
        (webm, _), _ = run(make(black_point=0.3, white_point=0.7), video=[clip], matte=[grey])
        frames = decode_rgba(webm)
        assert frames[:, :, 4:28, 3].max() <= 12
        assert frames[:, :, 36:60, 3].min() >= 243

    def test_a_matte_of_another_size_still_lines_up(self, clip, tmp_path):
        small = split_matte(tmp_path / "small.mp4", size=(32, 24))
        (webm, _), _ = run(make(), video=[clip], matte=[small])
        frames = decode_rgba(webm)
        assert frames.shape[1:3] == (SIZE[1], SIZE[0])
        assert frames[:, :, 4:28, 3].max() <= 12 and frames[:, :, 36:60, 3].min() >= 243

    def test_a_longer_matte_is_cut_to_the_clip(self, clip, tmp_path):
        long = split_matte(tmp_path / "long.mp4", frames=FRAMES + 5)
        (webm, _), _ = run(make(), video=[clip], matte=[long])
        assert decode_rgba(webm).shape[0] == FRAMES

    def test_max_frames_limits_the_output(self, clip, matte):
        (webm, _), _ = run(make(max_frames=3), video=[clip], matte=[matte])
        assert decode_rgba(webm).shape[0] == 3

    def test_an_odd_sized_clip_is_trimmed_to_even(self, tmp_path):
        odd = tmp_path / "odd.webm"
        subprocess.run(
            [require_ffmpeg(), "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=63x47:rate={FPS}",
             "-frames:v", "5", "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", str(odd)],
            check=True,
        )
        shade = split_matte(tmp_path / "odd_matte.mp4", size=(64, 48), frames=5)
        (webm, _), _ = run(make(), video=[str(odd)], matte=[shade])
        assert decode_rgba(webm).shape[1:3] == (46, 62)


class TestCheckerboardPreview:
    def test_the_preview_shows_the_checkerboard_where_transparent(self, clip, matte):
        (_, preview), _ = run(make(), video=[clip], matte=[matte])
        frame = decode_bgr(preview)[0]
        left = frame[8:12, 8:12].astype(int).mean()
        assert abs(left - 204) < 30 or abs(left - 153) < 30

    def test_the_preview_can_be_turned_off(self, clip, matte):
        outputs, _ = run(make(preview=False), video=[clip], matte=[matte])
        assert len(outputs) == 1 and outputs[0].endswith(".webm")

    def test_the_matte_is_passed_on_when_asked(self, clip, matte):
        outputs, _ = run(make(save_matte=True), video=[clip], matte=[matte])
        assert [Path(p).suffix for p in outputs] == [".webm", ".mp4", ".mp4"]
        assert outputs[-1] == matte


class TestFailures:
    def test_a_missing_vp9_encoder_is_reported_plainly(self, clip, matte, monkeypatch):
        monkeypatch.setattr(video_stream, "_has_encoder", lambda ffmpeg, name: False)
        with pytest.raises(GenerationExecutionError, match="libvpx-vp9"):
            run(make(), video=[clip], matte=[matte])

    def test_cancelling_removes_the_partial_files(self, clip, matte, tmp_path, monkeypatch):
        import tempfile

        scratch = tmp_path / "scratch"
        scratch.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(scratch))
        with pytest.raises(SamplingCancelled):
            run(make(), cancelled=lambda: True, video=[clip], matte=[matte])
        assert list(scratch.iterdir()) == []

    def test_progress_is_reported(self, clip, matte):
        _, emitted = run(make(), video=[clip], matte=[matte])
        assert any(isinstance(item, ProgressGenerationOutput) for item in emitted)
