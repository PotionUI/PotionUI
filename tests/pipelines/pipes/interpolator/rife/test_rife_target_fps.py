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

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GalleryGenerationOutput, ProgressGenerationOutput
from src.pipelines.pipes.interpolator.rife import main as rife_main
from src.pipelines.pipes.interpolator.rife.cadence import output_count, slot, source_per_output
from src.pipelines.pipes.interpolator.rife.main import RifeInterpolatorPipe
from tests.vendor.rife.layouts import NARROW_ENCODER_BLOCKS, published_head_layout, state_dict_for
from vendor.rife.ifnet import IFNet
from vendor.rife.inference import prepare_frame
from vendor.rife.loader import load_ifnet

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _ramp(index, size=(48, 32)):
    w, h = size
    x = np.linspace(0, 1, w)[None, :]
    y = np.linspace(0, 1, h)[:, None]
    shift = index * 0.03
    r = (np.sin((x + shift + 0 * y) * 6.0) * 0.5 + 0.5) * 255
    g = (y * 0.6 + 0.2 + 0 * x) * 255
    b = (np.cos((x - y + shift) * 4.0) * 0.5 + 0.5) * 255
    return np.stack([b, g, r], axis=-1).astype(np.uint8)


def _checker(size=(48, 32)):
    w, h = size
    tile = (np.indices((h, w)).sum(axis=0) // 4) % 2
    return np.repeat((tile * 255).astype(np.uint8)[..., None], 3, axis=2)


class _Capture:
    def __init__(self, frames, fps):
        self.frames = list(frames)
        self.fps = fps
        self.served = 0

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return len(self.frames)
        return 0

    def read(self):
        if self.served >= len(self.frames):
            return False, None
        self.served += 1
        return True, self.frames[self.served - 1].copy()

    def release(self):
        pass


class _Writer:
    last = None

    def __init__(self, out_path, width, height, fps, **kwargs):
        self.fps = fps
        self.frames = []
        _Writer.last = self

    def write(self, frame):
        self.frames.append(np.asarray(frame).copy())

    def close(self):
        pass

    def abort(self):
        pass


def _model():
    torch.manual_seed(0)
    return IFNet(NARROW_ENCODER_BLOCKS, (16, 4)).eval().float()


def _run(monkeypatch, frames, fps, model=None, **config):
    model = model or _model()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(cv2, "VideoCapture", lambda _path: _Capture(frames, fps))
    monkeypatch.setattr(rife_main, "StreamingMp4Writer", _Writer)
    monkeypatch.setattr(rife_main, "mux_audio_from_source", lambda *a, **k: False)
    monkeypatch.setattr(rife_main, "_load_model", lambda _p, _d, _m=None: model)
    outputs = []
    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "keep_audio": False, **config})
    pipe.process(PipeInput(input={"video": ["clip.mp4"]}), outputs.append)
    return _Writer.last, outputs


def _rgb(bgr):
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def _expected(model, frames, src_fps, factor=2, target_fps=0.0):
    ratio = source_per_output(src_fps, factor, target_fps)
    rgbs = [_rgb(frame) for frame in frames]
    out = []
    for k in range(output_count(len(rgbs), ratio)):
        s = slot(k, ratio)
        if s.left >= len(rgbs) - 1:
            out.append(rgbs[-1])
        elif s.t == 0.0:
            out.append(rgbs[s.left])
        else:
            tensor = lambda rgb: RifeInterpolatorPipe._to_tensor(rgb, "cpu", torch.float32)
            f0 = prepare_frame(model, tensor(rgbs[s.left]))
            f1 = prepare_frame(model, tensor(rgbs[s.left + 1]))
            out.append(RifeInterpolatorPipe._run(model, f0, f1, s.t, 1.0))
    return out


@pytest.mark.parametrize("src_fps,target_fps,n_frames,count", [
    (16.0, 24.0, 9, 14),
    (16.0, 25.0, 9, 14),
    (24.0, 60.0, 8, 20),
    (25.0, 60.0, 10, 24),
    (25.0, 24.0, 10, 10),
    (24.0, 48.0, 6, 12),
])
def test_target_rate_sets_count_rate_and_frames(monkeypatch, src_fps, target_fps, n_frames, count):
    frames = [_ramp(i) for i in range(n_frames)]
    model = _model()

    writer, outputs = _run(monkeypatch, frames, src_fps, model=model, target_fps=target_fps)

    assert writer.fps == target_fps
    assert len(writer.frames) == count == output_count(n_frames, source_per_output(src_fps, target_fps=target_fps))
    expected = _expected(model, frames, src_fps, target_fps=target_fps)
    assert all(np.array_equal(got, want) for got, want in zip(writer.frames, expected))
    gallery = [o for o in outputs if isinstance(o, GalleryGenerationOutput)]
    assert gallery[0].videos[0].fps == target_fps


@pytest.mark.parametrize("src_fps,config,rates", [
    (16.0, {"target_fps": 24.0}, "<<NUMBER:16.00 fps>> -> <<NUMBER:24.00 fps>>"),
    (10.0, {"factor": 2}, "<<NUMBER:10.00 fps>> -> <<NUMBER:20.00 fps>>"),
])
def test_the_progress_message_names_the_source_rate(monkeypatch, src_fps, config, rates):
    _, outputs = _run(monkeypatch, [_ramp(i) for i in range(5)], src_fps, **config)

    states = [o.state for o in outputs if isinstance(o, ProgressGenerationOutput)]
    assert any(state.startswith("Interpolating <<NUMBER:5 frames>> at ") and state.endswith(rates) for state in states)


def test_source_frames_pass_through_untouched_at_24_to_60(monkeypatch):
    frames = [_ramp(i) for i in range(6)]

    writer, _ = _run(monkeypatch, frames, 24.0, target_fps=60.0)

    for k in range(0, len(writer.frames), 5):
        assert np.array_equal(writer.frames[k], _rgb(frames[k * 2 // 5]))


@pytest.mark.parametrize("scene_detection", [True, False])
@pytest.mark.parametrize("clip", ["smooth", "cut"])
@pytest.mark.parametrize("factor", [2, 4])
def test_factor_mode_is_the_former_output(monkeypatch, factor, clip, scene_detection):
    frames = [_ramp(i) for i in range(5)] if clip == "smooth" else [_ramp(0), _ramp(1), _checker(), _checker(), _ramp(2)]
    model = _model()
    monkeypatch.setattr(RifeInterpolatorPipe, "_is_cut", staticmethod(lambda a, b: pytest.fail("factor mode looked for a cut")))

    writer, _ = _run(monkeypatch, frames, 10.0, model=model, factor=factor, scene_detection=scene_detection)

    rgbs = [_rgb(frame) for frame in frames]
    tensor = lambda rgb: RifeInterpolatorPipe._to_tensor(rgb, "cpu", torch.float32)
    former = [rgbs[0]]
    for i in range(1, len(rgbs)):
        f0, f1 = prepare_frame(model, tensor(rgbs[i - 1])), prepare_frame(model, tensor(rgbs[i]))
        former.extend(RifeInterpolatorPipe._run(model, f0, f1, f / factor, 1.0) for f in range(1, factor))
        former.append(rgbs[i])
    former.extend(rgbs[-1] for _ in range(factor - 1))
    assert writer.fps == 10.0 * factor
    assert len(writer.frames) == len(former) == 5 * factor
    assert all(np.array_equal(got, want) for got, want in zip(writer.frames, former))


def test_factor_is_ignored_once_a_target_rate_is_set(monkeypatch):
    writer, _ = _run(monkeypatch, [_ramp(i) for i in range(4)], 24.0, factor=3, target_fps=48.0)

    assert len(writer.frames) == 8


@pytest.mark.parametrize("target_fps", [-1.0, 241.0])
def test_an_out_of_range_target_is_refused(monkeypatch, target_fps):
    with pytest.raises(ValueError, match="target_fps"):
        _run(monkeypatch, [_ramp(0), _ramp(1)], 24.0, target_fps=target_fps)


@pytest.mark.parametrize("cut_after", [1, 2])
def test_a_cut_holds_the_nearer_frame_without_running_the_model(monkeypatch, cut_after):
    frames = [_ramp(i) for i in range(cut_after + 1)] + [_checker() for _ in range(5 - cut_after)]
    runs = []
    original = RifeInterpolatorPipe._run

    def spy(model, f0, f1, t, flow_scale):
        runs.append(t)
        return original(model, f0, f1, t, flow_scale)

    monkeypatch.setattr(RifeInterpolatorPipe, "_run", staticmethod(spy))

    writer, _ = _run(monkeypatch, frames, 16.0, target_fps=24.0)

    ratio = source_per_output(16.0, target_fps=24.0)
    across = [slot(k, ratio) for k in range(len(writer.frames)) if slot(k, ratio).left == cut_after and slot(k, ratio).t > 0]
    assert across
    for s in across:
        nearer = frames[cut_after] if s.t < 0.5 else frames[cut_after + 1]
        assert np.array_equal(writer.frames[s.index], _rgb(nearer))
    assert len(runs) == sum(1 for k in range(len(writer.frames))
                            if 0 < slot(k, ratio).t and slot(k, ratio).left not in (cut_after, 5))


def test_without_scene_detection_the_cut_is_blended(monkeypatch):
    frames = [_ramp(0), _checker()]

    writer, _ = _run(monkeypatch, frames, 24.0, target_fps=48.0, scene_detection=False)

    middle = writer.frames[1]
    assert not np.array_equal(middle, _rgb(frames[0])) and not np.array_equal(middle, _rgb(frames[1]))


def test_similar_frames_are_not_a_cut():
    a, b = _rgb(_ramp(0)), _rgb(_ramp(1))

    assert not RifeInterpolatorPipe._is_cut(a, b)
    assert RifeInterpolatorPipe._is_cut(a, _rgb(_checker()))


def test_a_much_lower_target_writes_exactly_its_count(monkeypatch):
    frames = [_ramp(i) for i in range(11)]

    writer, _ = _run(monkeypatch, frames, 60.0, target_fps=24.0)

    assert len(writer.frames) == output_count(11, source_per_output(60.0, target_fps=24.0)) == 4
    assert np.array_equal(writer.frames[0], _rgb(frames[0]))
    assert np.array_equal(writer.frames[2], _rgb(frames[5]))


def test_a_single_frame_clip_holds_it(monkeypatch):
    writer, _ = _run(monkeypatch, [_ramp(0)], 24.0, target_fps=60.0)

    assert len(writer.frames) == 2
    assert all(np.array_equal(frame, _rgb(_ramp(0))) for frame in writer.frames)


def test_a_head_line_model_runs_through_the_pipe(monkeypatch):
    model = load_ifnet(state_dict_for(published_head_layout("rife4.25")), "cpu")

    writer, _ = _run(monkeypatch, [_ramp(i) for i in range(4)], 24.0, model=model, target_fps=60.0)

    assert len(writer.frames) == 10
    assert writer.frames[1].shape == (32, 48, 3)


def _write_clip_with_sound(path, n_frames, fps, size=(64, 48)):
    duration = n_frames / fps
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size={size[0]}x{size[1]}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True)


def _probe(path):
    out = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,nb_frames,duration",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


@needs_ffmpeg
@pytest.mark.parametrize("src_fps,target_fps", [(24, 60), (16, 25)])
def test_real_encode_keeps_duration_and_sound(tmp_path, monkeypatch, src_fps, target_fps):
    source = tmp_path / "in.mp4"
    n_frames = 12
    _write_clip_with_sound(source, n_frames, src_fps)
    model = _model()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(rife_main, "_load_model", lambda _p, _d, _m=None: model)

    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "target_fps": float(target_fps), "keep_audio": True})
    result = pipe.process(PipeInput(input={"video": [str(source)]}), lambda o: None)

    streams = _probe(result.output["video"][0])
    assert "audio" in streams
    assert streams["video"]["r_frame_rate"] == f"{target_fps}/1"
    assert int(streams["video"]["nb_frames"]) == round(n_frames * target_fps / src_fps)
    assert float(streams["video"]["duration"]) == pytest.approx(n_frames / src_fps, abs=1.5 / target_fps)
