"""Tests for the interpolator/rife pipe: config spec, frame-count math, factor
validation, a synthetic streaming pass, and the real ffmpeg encode/mux path
(random weights -- frame count, fps and stream durations are asserted, not
visual quality).

Follows the video-pipe test convention: cv2 is guarded with
`pytest.importorskip("cv2", exc_type=ImportError)` -- the `exc_type` argument is
load-bearing (cv2 imports but fails loading its shared object in some
containers; without it collection aborts instead of skipping)."""

import json
import shutil
import subprocess

import numpy as np
import pytest

torch = pytest.importorskip("torch")
cv2 = pytest.importorskip(
    "cv2",
    reason="cv2 (or its native deps) not available",
    exc_type=ImportError,
)

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.interpolator.rife import main as rife_main
from src.pipelines.pipes.interpolator.rife.main import RifeInterpolatorPipe
from tests.vendor.rife.layouts import NARROW_NO_ENCODER_BLOCKS
from vendor.rife.ifnet import IFNet

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _stub_load_model(_path, device):
    """Stand in for `_load_model`, honouring its device/dtype contract -- the
    pipe moves input tensors to `device` and to the model's own dtype, so a stub
    that ignored the argument would only ever work on a CPU-only box."""
    torch.manual_seed(0)
    model = IFNet(NARROW_NO_ENCODER_BLOCKS, None).eval().to(device)
    return model.half() if device == "cuda" else model.float()


@pytest.fixture
def stub_model(monkeypatch):
    monkeypatch.setattr(rife_main, "_load_model", _stub_load_model)


def _write_input_video(path, n_frames=8, fps=10.0, size=(64, 64)):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, size)
    if not writer.isOpened():
        pytest.skip("no writable mp4 codec available for the synthetic input")
    rng = np.random.default_rng(0)
    for _ in range(n_frames):
        writer.write(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
    writer.release()
    cap = cv2.VideoCapture(str(path))
    decoded = 0
    if cap.isOpened():
        while True:
            ok, _ = cap.read()
            if not ok:
                break
            decoded += 1
    cap.release()
    if decoded < 2:
        pytest.skip("synthetic input video is not decodable in this environment")
    return decoded


def _write_input_video_with_audio(path, n_frames=12, fps=12.0, size=(64, 64)):
    duration = n_frames / fps
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i",
        f"testsrc=size={size[0]}x{size[1]}:rate={fps:g}:duration={duration:g}",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=44100:duration={duration:g}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True, capture_output=True)
    return path


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True,
    ).stdout
    probed = json.loads(out)
    streams = {s["codec_type"]: s for s in probed["streams"]}
    video = streams["video"]
    return {
        "container": float(probed["format"]["duration"]),
        "video": float(video["duration"]),
        "frames": int(video["nb_frames"]),
        "audio": float(streams["audio"]["duration"]) if "audio" in streams else None,
        "audio_frames": int(streams["audio"]["nb_frames"]) if "audio" in streams else None,
    }


class _FakeWriter:
    last = None

    def __init__(self, out_path, width, height, fps, **kwargs):
        self.width, self.height, self.fps = width, height, fps
        self.frames = []
        _FakeWriter.last = self

    def write(self, frame):
        self.frames.append(np.asarray(frame))

    def close(self):
        pass


# -- config / contract --------------------------------------------------------

def test_config_spec_matches_contract():
    specs = {s.name: s for s in RifeInterpolatorPipe.configuration()}
    assert set(specs) == {"model", "factor", "flow_scale", "keep_audio"}
    assert specs["model"].param_type is dict and specs["model"].required is True
    assert specs["factor"].default == 2 and specs["factor"].choices == [2, 4]
    assert specs["flow_scale"].default == 1.0 and specs["flow_scale"].choices == [1.0, 0.5]
    assert specs["keep_audio"].param_type is bool and specs["keep_audio"].default is True


def test_io_specs():
    ins = RifeInterpolatorPipe.inputs()
    outs = RifeInterpolatorPipe.outputs()
    assert [(s.name, s.io_type) for s in ins] == [("video", IOType.VIDEO)]
    assert [(s.name, s.io_type) for s in outs] == [("video", IOType.VIDEO)]
    assert RifeInterpolatorPipe.name == "interpolator/rife"


@pytest.mark.parametrize("n,factor,expected", [
    (8, 2, 16), (8, 4, 32), (2, 2, 4), (2, 4, 8), (1, 2, 2), (0, 2, 0),
])
def test_output_frame_count_math(n, factor, expected):
    # n*factor, not (n-1)*factor+1: the trailing frame is held for the slots it
    # owns so n*factor / (fps*factor) still equals the source's n/fps.
    assert RifeInterpolatorPipe.output_frame_count(n, factor) == expected


@pytest.mark.parametrize("n,factor,fps", [(12, 2, 12.0), (12, 4, 12.0), (25, 2, 25.0)])
def test_output_frame_count_preserves_duration(n, factor, fps):
    out_frames = RifeInterpolatorPipe.output_frame_count(n, factor)

    assert out_frames / (fps * factor) == pytest.approx(n / fps)


def test_invalid_factor_rejected(tmp_path, stub_model):
    video = tmp_path / "in.mp4"
    _write_input_video(video)
    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": 3})
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={"video": [str(video)]}), lambda o: None)


# -- end-to-end (streaming, fake encoder so no ffmpeg needed) -----------------

@pytest.mark.parametrize("factor", [2, 4])
def test_end_to_end_frame_count_and_fps(tmp_path, monkeypatch, stub_model, factor):
    video = tmp_path / "in.mp4"
    src_fps = 10.0
    decoded = _write_input_video(video, n_frames=8, fps=src_fps)

    monkeypatch.setattr(rife_main, "StreamingMp4Writer", _FakeWriter)
    monkeypatch.setattr(rife_main, "mux_audio_from_source", lambda *a, **k: False)

    pipe = RifeInterpolatorPipe({
        "model": {"file_path": "x", "name": "rife"},
        "factor": factor,
        "flow_scale": 1.0,
        "keep_audio": True,
    })
    result = pipe.process(PipeInput(input={"video": [str(video)]}), lambda o: None)

    expected = RifeInterpolatorPipe.output_frame_count(decoded, factor)
    assert len(_FakeWriter.last.frames) == expected
    assert _FakeWriter.last.fps == pytest.approx(src_fps * factor)
    assert result.output["video"] and isinstance(result.output["video"][0], str)


@pytest.mark.parametrize("factor", [2, 4])
def test_trailing_source_frame_is_held(tmp_path, monkeypatch, stub_model, factor):
    video = tmp_path / "in.mp4"
    decoded = _write_input_video(video, n_frames=6)

    monkeypatch.setattr(rife_main, "StreamingMp4Writer", _FakeWriter)
    monkeypatch.setattr(rife_main, "mux_audio_from_source", lambda *a, **k: False)

    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": factor})
    pipe.process(PipeInput(input={"video": [str(video)]}), lambda o: None)

    frames = _FakeWriter.last.frames
    assert len(frames) == decoded * factor
    # The clip ends on `factor` copies of the last decoded frame: the original
    # plus the factor-1 held ones.
    for held in frames[-factor:]:
        assert np.array_equal(held, frames[-1])


def test_cancellation_stops_early(tmp_path, monkeypatch, stub_model):
    video = tmp_path / "in.mp4"
    _write_input_video(video, n_frames=8)
    monkeypatch.setattr(rife_main, "StreamingMp4Writer", _FakeWriter)
    monkeypatch.setattr(rife_main, "mux_audio_from_source", lambda *a, **k: False)

    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": 2})

    # Cancel after a few source frames have been read: the loop breaks and the
    # partial clip is finalised rather than the full n*factor output.
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    result = pipe.process(
        PipeInput(input={"video": [str(video)]}),
        lambda o: None,
        is_cancelled=cancel,
    )
    assert 0 < len(_FakeWriter.last.frames) < RifeInterpolatorPipe.output_frame_count(8, 2)
    assert "video" in result.output


# -- end-to-end through real ffmpeg ------------------------------------------

@needs_ffmpeg
def test_end_to_end_real_ffmpeg_output(tmp_path, stub_model):
    video = tmp_path / "in.mp4"
    decoded = _write_input_video(video, n_frames=6, fps=8.0)

    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": 2, "keep_audio": True})
    result = pipe.process(PipeInput(input={"video": [str(video)]}), lambda o: None)
    out = result.output["video"][0]

    cap = cv2.VideoCapture(out)
    assert cap.isOpened()
    out_fps = cap.get(cv2.CAP_PROP_FPS)
    n = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        n += 1
    cap.release()
    assert n == RifeInterpolatorPipe.output_frame_count(decoded, 2)
    assert out_fps == pytest.approx(16.0, abs=0.5)


@needs_ffmpeg
@pytest.mark.parametrize("factor", [2, 4])
def test_duration_and_audio_survive_interpolation(tmp_path, stub_model, factor):
    # The regression this pins: emitting (n-1)*factor+1 frames left the video
    # (factor-1)/(fps*factor) short -- 41.667ms at 12fps/2x -- and `-shortest`
    # then trimmed the audio to match.
    n_frames, fps = 12, 12.0
    src = _write_input_video_with_audio(tmp_path / "in.mp4", n_frames=n_frames, fps=fps)
    source = _probe(src)
    assert source["frames"] == n_frames and source["audio"] is not None

    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": factor,
                                 "keep_audio": True})
    result = pipe.process(PipeInput(input={"video": [str(src)]}), lambda o: None)
    out = _probe(result.output["video"][0])

    shortfall = (factor - 1) / (fps * factor)
    assert out["frames"] == n_frames * factor
    assert out["video"] == pytest.approx(source["video"], abs=1e-3), (
        f"video is {source['video'] - out['video']:.6f}s short "
        f"(the defect was exactly {shortfall:.6f}s)"
    )
    assert out["audio"] == pytest.approx(source["audio"], abs=1e-3), (
        f"audio trimmed by {source['audio'] - out['audio']:.6f}s"
    )
    assert out["container"] == pytest.approx(source["container"], abs=1e-3)


@needs_ffmpeg
def test_muxed_audio_is_stream_copied(tmp_path, stub_model):
    # An AAC re-encode pads the track out to its 1024-sample frame grid, moving
    # the duration; a stream copy keeps the packet count identical.
    src = _write_input_video_with_audio(tmp_path / "in.mp4", n_frames=12, fps=12.0)

    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": 2, "keep_audio": True})
    result = pipe.process(PipeInput(input={"video": [str(src)]}), lambda o: None)

    assert _probe(result.output["video"][0])["audio_frames"] == _probe(src)["audio_frames"]
