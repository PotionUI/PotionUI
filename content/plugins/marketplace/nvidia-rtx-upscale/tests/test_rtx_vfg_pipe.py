import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from src.plugin_api.pipes import PipeInput

cv2 = pytest.importorskip("cv2", reason="cv2 (or its native deps) not available", exc_type=ImportError)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)

needs_ffmpeg_binary = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


@pytest.fixture
def pipe_class(vfg_module):
    return vfg_module.RtxFrameGenerationPipe


def _interpolated(entries):
    return [entry for entry in entries if entry[1] is not None]


def test_the_frame_rate_is_the_target_or_the_multiplied_source(vfg_module):
    assert vfg_module.output_fps(24.0, 2, 0) == 48.0
    assert vfg_module.output_fps(24.0, 4, 0) == 96.0
    assert vfg_module.output_fps(25.0, 2, 60) == 60.0


@pytest.mark.parametrize("count,source,target,expected", [
    (81, 16.0, 24.0, 122),
    (120, 24.0, 60.0, 300),
    (125, 25.0, 60.0, 300),
    (125, 25.0, 24.0, 120),
    (10, 24.0, 48.0, 20),
    (1, 24.0, 60.0, 2),
])
def test_the_output_keeps_the_duration(vfg_module, count, source, target, expected):
    assert vfg_module.output_count(count, source, target) == expected
    assert abs(expected / target - count / source) <= 1 / target


def test_sixteen_to_twenty_four_keeps_one_frame_in_three(vfg_module):
    entries = vfg_module.plan(81, 16.0, 24.0)

    assert entries[:7] == [
        (0, None),
        (0, pytest.approx(2 / 3)),
        (1, pytest.approx(1 / 3)),
        (2, None),
        (2, pytest.approx(2 / 3)),
        (3, pytest.approx(1 / 3)),
        (4, None),
    ]
    assert {round(step, 6) for _, step in _interpolated(entries)} == {round(1 / 3, 6), round(2 / 3, 6)}
    assert entries[-1] == (80, None)


def test_twenty_four_to_sixty_makes_four_frames_in_five(vfg_module):
    entries = vfg_module.plan(120, 24.0, 60.0)

    assert len(entries) == 300
    assert entries[:6] == [
        (0, None),
        (0, pytest.approx(0.4)),
        (0, pytest.approx(0.8)),
        (1, pytest.approx(0.2)),
        (1, pytest.approx(0.6)),
        (2, None),
    ]
    assert {round(step, 6) for _, step in _interpolated(entries)} == {0.2, 0.4, 0.6, 0.8}
    assert entries[-2:] == [(119, None), (119, None)]


def test_twenty_five_to_sixty_uses_eleven_instants(vfg_module):
    entries = vfg_module.plan(125, 25.0, 60.0)
    steps = {Fraction(step).limit_denominator(12) for _, step in _interpolated(entries)}

    assert len(entries) == 300
    assert steps == {Fraction(k, 12) for k in range(1, 12)}
    assert sum(1 for _, step in entries if step is None) == 27
    assert entries[-2:] == [(124, None), (124, None)]


def test_a_lower_target_still_walks_the_clip(vfg_module):
    entries = vfg_module.plan(125, 25.0, 24.0)

    assert len(entries) == 120
    indices = [index for index, _ in entries]
    assert indices == sorted(indices)
    assert all(0 <= index <= 124 for index in indices)
    assert all(0.0 < step < 1.0 for _, step in _interpolated(entries))


@pytest.mark.parametrize("factor", [2, 4])
def test_a_multiplier_holds_the_last_frame(vfg_module, factor):
    count = 6
    entries = vfg_module.plan(count, 24.0, vfg_module.output_fps(24.0, factor, 0))

    assert len(entries) == count * factor
    expected = []
    for index in range(count - 1):
        expected.append((index, None))
        expected.extend((index, pytest.approx(j / factor)) for j in range(1, factor))
    expected.extend([(count - 1, None)] * factor)
    assert entries == expected


def test_an_instant_next_to_a_source_frame_takes_that_frame(vfg_module):
    assert vfg_module.plan_frame(1, 10, 2.0 - 1e-9, 1.0) == (2, None)
    assert vfg_module.plan_frame(1, 10, 2.0 + 1e-5, 1.0) == (2, None)
    assert vfg_module.plan_frame(1, 10, 2.0 + 2e-3, 1.0) == (2, pytest.approx(2e-3))
    assert [vfg_module.plan_frame(k, 10, 24.0, 24.0) for k in range(10)] == [(k, None) for k in range(10)]


def test_the_config_rejects_what_the_sdk_cannot_do(vfg_module, pipe_class):
    for config in ({"factor": 3}, {"target_fps": -1}, {"target_fps": 500}, {"mode": "ultra"}):
        with pytest.raises(vfg_module.GenerationExecutionError):
            pipe_class({**pipe_class.get_default_config(), **config})._settings()


def test_a_target_frame_rate_makes_the_factor_irrelevant(pipe_class):
    pipe = pipe_class({**pipe_class.get_default_config(), "factor": 3, "target_fps": 60})

    assert pipe._settings()[:2] == (3, 60.0)


def test_an_nvvfx_without_frame_generation_is_refused(vfg_module, fake_nvvfx_without_frame_generation):
    with pytest.raises(vfg_module.GenerationExecutionError) as exc_info:
        vfg_module._VfgSession(fake_nvvfx_without_frame_generation, "medium", True, 2)

    assert "nvidia-vfx 0.2" in str(exc_info.value)


def test_the_mode_and_the_shot_detection_reach_the_sdk(vfg_module, fake_nvvfx):
    vfg_module._VfgSession(fake_nvvfx, "high", False, None)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]

    assert effect.mode == fake_nvvfx.VideoFrameGeneration.Mode.HIGH
    assert effect.scene_detection is False


def test_each_made_frame_is_copied_out_of_the_sdk_buffer(vfg_module, fake_nvvfx, no_real_cuda):
    session = vfg_module._VfgSession(fake_nvvfx, "medium", True, None)
    frames = {
        0: np.zeros((8, 8, 3), dtype=np.uint8),
        1: np.full((8, 8, 3), 200, dtype=np.uint8),
    }
    session.ensure_loaded(8, 8)

    first = session.frame_between(frames, 0, 0.25).copy()
    second = session.frame_between(frames, 0, 0.75)

    assert int(first[0, 0, 0]) == 50
    assert int(second[0, 0, 0]) == 150
    np.testing.assert_array_equal(session.frame_between(frames, 0, 0.25), first)


def test_a_multiplier_runs_the_sdk_by_frame_index(vfg_module, fake_nvvfx, no_real_cuda):
    session = vfg_module._VfgSession(fake_nvvfx, "medium", True, 4)
    frames = {0: np.zeros((8, 8, 3), dtype=np.uint8), 1: np.zeros((8, 8, 3), dtype=np.uint8)}
    session.ensure_loaded(8, 8)
    for step in (0.25, 0.5, 0.75):
        session.frame_between(frames, 0, step)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]

    assert effect.frame_multiplier == 4
    assert effect.calls == [("index", 1), ("index", 2), ("index", 3)]


def test_a_load_failure_names_the_size(vfg_module, fake_nvvfx):
    session = vfg_module._VfgSession(fake_nvvfx, "medium", True, None)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]

    def refuse():
        raise RuntimeError("NvVFX_Load failed (code -7)")

    effect.load = refuse
    with pytest.raises(vfg_module.GenerationExecutionError) as exc_info:
        session.ensure_loaded(64, 48)

    assert "64x48" in str(exc_info.value)


def test_a_missing_video_is_refused(vfg_module, pipe_class, fake_nvvfx):
    with pytest.raises(vfg_module.GenerationExecutionError):
        pipe_class(pipe_class.get_default_config()).process(PipeInput(input={}), lambda o: None)


def _write_input_video(path, frames=8, fps=10.0, size=(64, 48)):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        pytest.skip("no writable mp4 codec available for the synthetic input")
    for index in range(frames):
        writer.write(np.full((size[1], size[0], 3), index * 20, dtype=np.uint8))
    writer.release()


def _write_input_video_with_audio(path, frames=12, fps=12.0, size=(64, 48)):
    duration = frames / fps
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc=size={size[0]}x{size[1]}:rate={fps:g}:duration={duration:g}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration:g}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True, capture_output=True)


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True,
    ).stdout
    probed = json.loads(out)
    streams = {stream["codec_type"]: stream for stream in probed["streams"]}
    video = streams["video"]
    num, den = video["avg_frame_rate"].split("/")
    return {
        "frames": int(video["nb_read_frames"]),
        "fps": float(num) / float(den),
        "duration": float(probed["format"]["duration"]),
        "audio": float(streams["audio"]["duration"]) if "audio" in streams else None,
    }


@needs_ffmpeg
def test_a_target_frame_rate_converts_a_clip(tmp_path, pipe_class, fake_nvvfx, no_real_cuda):
    source = tmp_path / "in.mp4"
    _write_input_video(source, frames=8, fps=10.0)
    outputs = []
    pipe = pipe_class({**pipe_class.get_default_config(), "target_fps": 15, "keep_audio": False})

    result = pipe.process(PipeInput(input={"video": [str(source)]}), outputs.append)

    (path,) = result.output["video"]
    probed = _probe(path)
    assert probed["frames"] == 12
    assert probed["fps"] == pytest.approx(15.0)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]
    assert [kind for kind, _ in effect.calls] == ["timestep"] * len(effect.calls)
    assert {round(step, 6) for _, step in effect.calls} == {round(2 / 3, 6), round(1 / 3, 6)}
    assert effect.closed
    gallery = [o for o in outputs if type(o).__name__ == "GalleryGenerationOutput"]
    assert gallery and gallery[0].videos[0].fps == pytest.approx(15.0)


@needs_ffmpeg
def test_a_multiplier_keeps_the_sound_and_the_duration(tmp_path, pipe_class, fake_nvvfx, no_real_cuda):
    source = tmp_path / "in.mp4"
    _write_input_video_with_audio(source, frames=12, fps=12.0)
    pipe = pipe_class({**pipe_class.get_default_config(), "factor": 2, "target_fps": 0, "keep_audio": True})

    result = pipe.process(PipeInput(input={"video": [str(source)]}), lambda o: None)

    (path,) = result.output["video"]
    probed = _probe(path)
    original = _probe(source)
    assert probed["frames"] == original["frames"] * 2
    assert probed["fps"] == pytest.approx(original["fps"] * 2)
    assert probed["audio"] is not None
    assert probed["duration"] == pytest.approx(original["duration"], abs=0.1)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]
    assert effect.frame_multiplier == 2
    assert {call for call in effect.calls} == {("index", 1)}


def _frames(width=8, height=8):
    return {0: np.zeros((height, width, 3), dtype=np.uint8), 1: np.full((height, width, 3), 200, dtype=np.uint8)}


@pytest.mark.parametrize("code", [-17, -18, -19])
def test_an_unsupported_gpu_names_the_generations_it_needs(vfg_module, fake_nvvfx, code):
    session = vfg_module._VfgSession(fake_nvvfx, "medium", True, None)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]

    def refuse():
        raise RuntimeError(f"NvVFX_Load failed: The GPU is not supported (code {code})")

    effect.load = refuse
    with pytest.raises(vfg_module.GenerationExecutionError) as exc_info:
        session.ensure_loaded(64, 48)

    message = str(exc_info.value)
    assert "does not run on this GPU or driver for 64x48 frames" in message
    assert "Ada or Blackwell" in message


def test_a_failed_frame_names_the_source_pair(vfg_module, fake_nvvfx, no_real_cuda):
    session = vfg_module._VfgSession(fake_nvvfx, "medium", True, None)
    session.ensure_loaded(8, 8)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]

    def refuse(*args, **kwargs):
        raise RuntimeError("NvVFX_Run failed: inference error (code -120)")

    effect.run_at_timestep = refuse
    with pytest.raises(vfg_module.GenerationExecutionError) as exc_info:
        session.frame_between(_frames(), 0, 0.5)

    assert str(exc_info.value) == (
        "NVIDIA RTX Video Frame Generation failed between frames 0 and 1: "
        "NvVFX_Run failed: inference error (code -120)"
    )


def test_a_quadruple_multiplier_is_set_before_load(vfg_module, fake_nvvfx, no_real_cuda):
    session = vfg_module._VfgSession(fake_nvvfx, "medium", True, 4)
    session.ensure_loaded(8, 8)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]

    assert (effect.input_width, effect.input_height, effect.frame_multiplier) == (8, 8, 4)
    assert effect.load_calls == 1
    session.ensure_loaded(8, 8)
    assert effect.load_calls == 1


def test_a_target_frame_rate_leaves_the_multiplier_alone(vfg_module, fake_nvvfx, no_real_cuda):
    session = vfg_module._VfgSession(fake_nvvfx, "medium", True, None)
    session.ensure_loaded(8, 8)
    session.frame_between(_frames(), 0, 0.4)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]

    assert effect.frame_multiplier == 2
    assert effect.calls == [("timestep", pytest.approx(0.4))]


class _RecordingWriter:
    instances = []
    abort_error = None

    def __init__(self, path, width, height, fps):
        self.path = path
        self.calls = []
        _RecordingWriter.instances.append(self)

    def write(self, frame):
        self.calls.append("write")

    def close(self):
        self.calls.append("close")

    def abort(self):
        self.calls.append("abort")
        if _RecordingWriter.abort_error is not None:
            raise _RecordingWriter.abort_error


@pytest.mark.parametrize("abort_error", [None, RuntimeError("abort failed too")])
def test_a_failure_mid_clip_aborts_the_writer_and_keeps_the_error(
    vfg_module, pipe_class, fake_nvvfx, no_real_cuda, tmp_path, monkeypatch, abort_error,
):
    source = tmp_path / "in.mp4"
    _write_input_video(source, frames=4, fps=10.0)

    def refuse(*args, **kwargs):
        raise RuntimeError("boom")

    _RecordingWriter.instances = []
    monkeypatch.setattr(_RecordingWriter, "abort_error", abort_error)
    monkeypatch.setattr(vfg_module, "StreamingMp4Writer", _RecordingWriter)
    monkeypatch.setattr(fake_nvvfx.VideoFrameGeneration, "run_at_timestep", refuse)
    pipe = pipe_class({**pipe_class.get_default_config(), "target_fps": 15, "keep_audio": False})

    with pytest.raises(vfg_module.GenerationExecutionError) as exc_info:
        pipe.process(PipeInput(input={"video": [str(source)]}), lambda o: None)

    assert str(exc_info.value) == "NVIDIA RTX Video Frame Generation failed between frames 0 and 1: boom"
    (writer,) = _RecordingWriter.instances
    assert writer.calls == ["write", "abort"]
    assert not (tmp_path / writer.path).exists()
    assert fake_nvvfx.VideoFrameGeneration.instances[-1].closed


@needs_ffmpeg_binary
def test_a_muxed_clip_leaves_no_silent_copy_behind(tmp_path, pipe_class, fake_nvvfx, no_real_cuda, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    source = tmp_path / "in.mp4"
    _write_input_video_with_audio(source, frames=12, fps=12.0)
    pipe = pipe_class({**pipe_class.get_default_config(), "factor": 2, "target_fps": 0, "keep_audio": True})

    result = pipe.process(PipeInput(input={"video": [str(source)]}), lambda o: None)

    (path,) = result.output["video"]
    assert sorted(p.name for p in (tmp_path / "tmp").iterdir()) == [Path(path).name]


def test_frames_are_made_on_the_default_cuda_stream(vfg_module):
    from types import SimpleNamespace

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True,
        default_stream=lambda: "stream 0",
        stream=lambda stream: ("on", stream),
    ))

    assert vfg_module.default_stream(fake_torch) == ("on", "stream 0")


def test_the_sdk_and_the_copies_run_inside_the_stream_context(vfg_module, fake_nvvfx, no_real_cuda, monkeypatch):
    import contextlib

    entered = []

    @contextlib.contextmanager
    def recording(torch_module):
        entered.append("in")
        yield
        entered.append("out")

    monkeypatch.setattr(vfg_module, "default_stream", recording)
    session = vfg_module._VfgSession(fake_nvvfx, "medium", True, None)
    session.ensure_loaded(8, 8)
    effect = fake_nvvfx.VideoFrameGeneration.instances[-1]
    real_run = effect.run_at_timestep

    def run(*args, **kwargs):
        assert entered == ["in"]
        return real_run(*args, **kwargs)

    effect.run_at_timestep = run
    made = session.frame_between(_frames(), 0, 0.5)

    assert entered == ["in", "out"]
    assert int(made[0, 0, 0]) == 100


@pytest.mark.parametrize("version", ["0.2.0.0", None])
def test_the_import_tells_an_installed_but_unloadable_nvvfx_from_a_missing_one(vfg_module, monkeypatch, version):
    import importlib.metadata
    import sys

    def lookup(name):
        if version is None:
            raise importlib.metadata.PackageNotFoundError(name)
        return version

    class Refuse:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] == "nvvfx":
                raise ImportError("Failed to import NVVFX C++ extension. ")
            return None

    monkeypatch.setattr(importlib.metadata, "version", lookup)
    monkeypatch.delitem(sys.modules, "nvvfx", raising=False)
    finder = Refuse()
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(vfg_module.GenerationExecutionError) as exc_info:
            vfg_module._import_nvvfx()
    finally:
        sys.meta_path.remove(finder)

    message = str(exc_info.value)
    if version is None:
        assert message.startswith("NVIDIA RTX Video Frame Generation is not installed. pip install")
    else:
        assert message.startswith("NVIDIA RTX Video Frame Generation is installed (nvidia-vfx 0.2.0.0) but its library does not load: Failed to import")
        assert "--gpus all" in message


def test_an_effect_the_sdk_cannot_create_is_explained(vfg_module, fake_nvvfx, monkeypatch):
    def refuse(self, *args, **kwargs):
        raise RuntimeError("NvVFX_CreateEffect failed: The GPU is not supported (code -17)")

    monkeypatch.setattr(fake_nvvfx.VideoFrameGeneration, "__init__", refuse)

    with pytest.raises(vfg_module.GenerationExecutionError) as exc_info:
        vfg_module._VfgSession(fake_nvvfx, "medium", True, None)

    assert str(exc_info.value).startswith(
        "NVIDIA RTX Video Frame Generation does not run on this GPU or driver when the effect is created: it needs an NVIDIA Ada or Blackwell GPU"
    )
