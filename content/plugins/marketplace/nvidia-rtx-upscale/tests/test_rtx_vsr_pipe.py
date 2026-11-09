"""Behavioral tests for the `upscaler/rtx_vsr` pipe.

Every test here mocks `nvvfx` entirely (see `fake_nvvfx` in conftest.py) and
mocks `Tensor.cuda()` to a no-op (`no_real_cuda`) - the real GPU on this host
is shared and busy, and nvvfx is not installed in this venv at all.
"""

import io
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.plugin_api.pipes import PipeInput
from src.plugin_api.sampling import SamplingCancelled


@pytest.fixture
def pipe_class(pipe_module):
    return pipe_module.RtxUpscalePipe


def _rgb_image(width: int, height: int) -> Image.Image:
    return Image.fromarray(np.zeros((height, width, 3), dtype=np.uint8), mode="RGB")


# -- round_to_8 / target_size --------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (64, 64),
    (65, 64),   # 65/8 = 8.125 -> round -> 8 -> 64
    (100, 96),  # 100/8 = 12.5 -> banker's rounding -> 12 -> 96
    (4, 8),     # rounds to 0 -> floored up to the 8px minimum
    (0, 8),
])
def test_round_to_8(pipe_module, value, expected):
    assert pipe_module.round_to_8(value) == expected


def test_target_size_forces_input_dims_for_denoise_and_deblur(pipe_module):
    """Denoise/deblur are same-resolution enhancement - `scale` must have no
    effect on the output size, even when it's far from 1.0."""
    for intent in ("denoise", "deblur"):
        assert pipe_module.target_size(intent, 4.0, 100, 200) == (96, 200)
        assert pipe_module.target_size(intent, 1.0, 100, 200) == (96, 200)


def test_target_size_scales_for_upscale_intents(pipe_module):
    for intent in ("upscale", "upscale_highbitrate"):
        assert pipe_module.target_size(intent, 2.0, 100, 200) == (
            pipe_module.round_to_8(200), pipe_module.round_to_8(400)
        )


def test_scale_config_declares_the_4x_cap(pipe_class):
    """`scale`'s min/max drive the shared config validator's cap - the pipe
    doesn't hand-roll a range check of its own."""
    spec = next(s for s in pipe_class.configuration() if s.name == "scale")

    assert spec.min_value == 1.0
    assert spec.max_value == 4.0


# -- quality mapping -------------------------------------------------------

def test_quality_mapping_covers_every_intent_and_tier(pipe_module, fake_nvvfx):
    expected = {
        ("upscale", "low"): "LOW",
        ("upscale", "medium"): "MEDIUM",
        ("upscale", "high"): "HIGH",
        ("upscale", "ultra"): "ULTRA",
        ("upscale_highbitrate", "low"): "HIGHBITRATE_LOW",
        ("upscale_highbitrate", "ultra"): "HIGHBITRATE_ULTRA",
        ("denoise", "low"): "DENOISE_LOW",
        ("denoise", "ultra"): "DENOISE_ULTRA",
        ("deblur", "low"): "DEBLUR_LOW",
        ("deblur", "ultra"): "DEBLUR_ULTRA",
    }
    for (intent, quality), attr_name in expected.items():
        level = pipe_module._quality_level(fake_nvvfx, intent, quality)
        assert level == getattr(fake_nvvfx.VideoSuperRes.QualityLevel, attr_name)
        assert level != fake_nvvfx.VideoSuperRes.QualityLevel.BICUBIC


def test_quality_mapping_falls_back_to_effects_namespace(pipe_module):
    """The reference ComfyUI node reads `nvvfx.effects.QualityLevel` instead of
    `VideoSuperRes.QualityLevel` - both spellings must resolve."""
    from enum import IntEnum
    from types import ModuleType, SimpleNamespace

    class QL(IntEnum):
        ULTRA = 4

    module = ModuleType("nvvfx")
    module.effects = SimpleNamespace(QualityLevel=QL)
    # No `VideoSuperRes` attribute at all on this fake module.

    level = pipe_module._quality_level(module, "upscale", "ultra")
    assert level == QL.ULTRA


def test_quality_mapping_missing_nvvfx_enum_raises_actionable_error(pipe_module):
    from types import ModuleType

    module = ModuleType("nvvfx")  # neither VideoSuperRes nor effects

    with pytest.raises(pipe_module.GenerationExecutionError):
        pipe_module._quality_level(module, "upscale", "ultra")


# -- missing nvvfx -----------------------------------------------------

def test_missing_nvvfx_raises_actionable_error(pipe_module, blocking_finder_cls, monkeypatch):
    finder = blocking_finder_cls(["nvvfx"])
    monkeypatch.delitem(sys.modules, "nvvfx", raising=False)
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(pipe_module.GenerationExecutionError) as exc_info:
            pipe_module._import_nvvfx()
    finally:
        sys.meta_path.remove(finder)

    message = str(exc_info.value)
    assert 'pip install "nvidia-vfx>=0.2" --extra-index-url https://pypi.nvidia.com' in message
    assert "570.190" in message
    assert "LD_LIBRARY_PATH=/usr/lib/wsl/lib" in message
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility,ngx" in message


# -- session: reload-on-size-change and clone-before-next-run ------------

def test_ensure_loaded_reloads_only_on_size_change(pipe_module, fake_nvvfx):
    level = fake_nvvfx.VideoSuperRes.QualityLevel.ULTRA
    session = pipe_module._RtxSession(fake_nvvfx, level)
    effect = fake_nvvfx.VideoSuperRes.instances[-1]

    session.ensure_loaded(64, 64)
    assert effect.load_calls == 1

    session.ensure_loaded(64, 64)  # same size -> no reload
    assert effect.load_calls == 1

    session.ensure_loaded(128, 64)  # different size -> reload
    assert effect.load_calls == 2


def test_clone_dlpack_result_copies_before_the_buffer_is_reused(pipe_module):
    """Isolates the exact hazard NVIDIA's own `VideoSuperRes.run()` docstring
    warns about: "you must copy/clone it immediately before the next call or
    close()." A plain `torch.from_dlpack(result.image)` WITHOUT `.clone()`
    would return a view of the same storage nvvfx reuses for its next
    `run()` -- this simulates that reuse directly (mutating the same buffer
    object in place) rather than going through the full pipe, whose later
    dtype-converting ops happen to copy anyway and would mask a missing
    clone here."""
    import torch
    from types import SimpleNamespace

    buffer = torch.zeros(3, 4, 4)
    result = SimpleNamespace(image=buffer)

    cloned = pipe_module._clone_dlpack_result(result)
    buffer.fill_(1.0)  # simulates nvvfx reusing/overwriting this buffer on the next run()

    assert torch.all(cloned == 0.0), (
        "the cloned tensor changed when the source buffer was mutated -- "
        "_clone_dlpack_result is not actually copying the data out"
    )


def test_process_frame_produces_independent_output_per_call(pipe_module, fake_nvvfx, no_real_cuda):
    """End-to-end companion to the isolated clone test above: two frames
    processed through the same session/effect must not observe each other's
    data, even though the fake effect reuses one buffer across `run()` calls."""
    level = fake_nvvfx.VideoSuperRes.QualityLevel.ULTRA
    session = pipe_module._RtxSession(fake_nvvfx, level)
    session.ensure_loaded(8, 8)

    first = session.process_frame(_rgb_image(8, 8))
    first_pixels = np.array(first).copy()

    session.process_frame(_rgb_image(8, 8))

    assert np.all(first_pixels == 0), "the first frame's already-returned pixels must not change later"


# -- full process(): denoise/deblur ignore scale, cancellation -----------

def test_process_image_denoise_ignores_scale(pipe_class, fake_nvvfx, no_real_cuda):
    pipe = pipe_class({"intent": "denoise", "quality": "high", "scale": 4.0})
    outputs = []
    result = pipe.process(
        PipeInput(input={"image": [_rgb_image(40, 24)]}),
        outputs.append,
    )

    (out_image,) = result.output["image"]
    assert out_image.size == (40, 24)


def test_process_image_upscale_applies_scale(pipe_class, fake_nvvfx, no_real_cuda):
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0})
    outputs = []
    result = pipe.process(
        PipeInput(input={"image": [_rgb_image(40, 24)]}),
        outputs.append,
    )

    (out_image,) = result.output["image"]
    assert out_image.size == (80, 48)


def test_process_requires_image_or_video(pipe_module, pipe_class, fake_nvvfx):
    pipe = pipe_class(pipe_class.get_default_config())

    with pytest.raises(pipe_module.GenerationExecutionError):
        pipe.process(PipeInput(input={}), lambda o: None)


class _CancelAfter:
    def __init__(self, calls):
        self.calls = calls
        self.seen = 0

    def __call__(self):
        self.seen += 1
        return self.seen > self.calls


def _galleries(outputs):
    return [o for o in outputs if type(o).__name__ == "GalleryGenerationOutput"]


def test_a_cancel_between_images_publishes_none_of_them(pipe_class, fake_nvvfx, no_real_cuda):
    outputs = []
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0})
    images = [_rgb_image(16, 16), _rgb_image(16, 16), _rgb_image(16, 16)]

    with pytest.raises(SamplingCancelled):
        pipe.process(PipeInput(input={"image": images}), outputs.append, _CancelAfter(1))

    effect = fake_nvvfx.VideoSuperRes.instances[-1]
    assert effect.run_calls == 1
    assert effect.closed
    assert _galleries(outputs) == []


class _ChattyFfmpeg:
    def __init__(self, flood_bytes):
        read_fd, write_fd = os.pipe()
        self.stdin = io.BytesIO()
        self.stderr = os.fdopen(read_fd, "rb")
        self.returncode = None
        self._write_fd = write_fd
        self._flood = threading.Thread(target=self._chatter, args=(flood_bytes,), daemon=True)
        self._flood.start()

    def _chatter(self, flood_bytes):
        with os.fdopen(self._write_fd, "wb") as sink:
            sink.write(b"E" * flood_bytes)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._flood.join(timeout=2)
        if self._flood.is_alive():
            raise subprocess.TimeoutExpired(cmd="chatty-ffmpeg", timeout=timeout)
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def test_the_pipe_writes_video_through_the_shared_writer(pipe_module, monkeypatch):
    from src.plugin_api import media

    assert pipe_module.StreamingMp4Writer is media.StreamingMp4Writer

    proc = _ChattyFfmpeg(flood_bytes=400_000)
    video_stream = sys.modules["src.platform.util.video_stream"]
    monkeypatch.setattr(video_stream, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(video_stream.subprocess, "Popen", lambda *a, **k: proc)

    writer = pipe_module.StreamingMp4Writer("out.mp4", 8, 8, 24.0)
    writer.write(np.zeros((8, 8, 3), dtype=np.uint8))
    writer.close()

    assert proc.returncode == 0


@pytest.mark.parametrize("width,height,target,expected", [
    (832, 480, 1080, (1872, 1080)),
    (768, 448, 1080, (1848, 1080)),
    (480, 832, 1080, (1080, 1872)),
    (832, 480, 1440, (2496, 1440)),
    (832, 480, 4000, (3328, 1920)),
    (832, 480, 240, (832, 480)),
])
def test_target_short_side_sets_the_scale(pipe_module, width, height, target, expected):
    assert pipe_module.target_size("upscale", 2.0, width, height, target) == expected


def test_target_short_side_is_ignored_by_denoise_and_deblur(pipe_module):
    for intent in ("denoise", "deblur"):
        assert pipe_module.target_size(intent, 2.0, 832, 480, 1080) == (832, 480)


def test_zero_target_short_side_keeps_the_scale(pipe_module):
    assert pipe_module.target_size("upscale", 2.0, 832, 480, 0) == (1664, 960)
    assert pipe_module.target_size("upscale", 2.0, 832, 480) == (1664, 960)


@pytest.mark.parametrize("width,height", [(100, 60), (101, 57), (833, 481), (834, 482), (7, 9)])
def test_an_upscale_never_lands_below_the_source(pipe_module, width, height):
    out_w, out_h = pipe_module.target_size("upscale", 1.0, width, height)

    assert out_w >= width and out_h >= height
    assert out_w % 8 == 0 and out_h % 8 == 0
    assert out_w - width < 8 and out_h - height < 8


def test_a_side_that_rounded_down_now_rounds_up(pipe_module):
    assert pipe_module.round_to_8(834) == 832
    assert pipe_module.target_size("upscale", 1.0, 834, 482) == (840, 488)
    assert pipe_module.target_size("upscale_highbitrate", 1.0, 834, 482) == (840, 488)


def test_target_short_side_and_strength_are_declared(pipe_class):
    specs = {spec.name: spec for spec in pipe_class.configuration()}
    defaults = pipe_class.get_default_config()

    assert (specs["target_short_side"].default, defaults["target_short_side"]) == (0, 0)
    assert (specs["target_short_side"].min_value, specs["target_short_side"].max_value) == (0, 8192)
    assert (specs["strength"].default, defaults["strength"]) == (1.0, 1.0)
    assert (specs["strength"].min_value, specs["strength"].max_value) == (0.0, 1.0)


def test_process_image_reaches_the_target_short_side(pipe_class, fake_nvvfx, no_real_cuda):
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 1.0, "target_short_side": 48})
    result = pipe.process(PipeInput(input={"image": [_rgb_image(40, 24)]}), lambda o: None)

    (out_image,) = result.output["image"]
    assert out_image.size == (80, 48)


@pytest.mark.parametrize("strength", [1.0, "1.0", None, ""])
def test_full_strength_is_not_sent_to_the_sdk(pipe_class, fake_nvvfx, no_real_cuda, strength):
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0, "strength": strength})
    pipe.process(PipeInput(input={"image": [_rgb_image(16, 16)]}), lambda o: None)

    assert "strength" not in fake_nvvfx.VideoSuperRes.instances[-1].kwargs


@pytest.mark.parametrize("strength", [0.4, "0.4", 0.0])
def test_a_lower_strength_reaches_the_sdk(pipe_class, fake_nvvfx, no_real_cuda, strength):
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0, "strength": strength})
    pipe.process(PipeInput(input={"image": [_rgb_image(16, 16)]}), lambda o: None)

    assert fake_nvvfx.VideoSuperRes.instances[-1].kwargs["strength"] == pytest.approx(float(strength))


def test_strength_falls_back_when_the_sdk_takes_none(pipe_class, fake_nvvfx_without_strength, no_real_cuda, caplog):
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0, "strength": 0.4})
    with caplog.at_level("WARNING"):
        result = pipe.process(PipeInput(input={"image": [_rgb_image(16, 16)]}), lambda o: None)

    assert result.output["image"][0].size == (32, 32)
    assert "strength" not in fake_nvvfx_without_strength.VideoSuperRes.instances[-1].kwargs
    assert "takes no strength" in caplog.text


def _failing_process(pipe_module, pipe_class, fake_nvvfx, error, at="load"):
    setattr(fake_nvvfx.VideoSuperRes, f"fail_{at}", error)
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0})
    with pytest.raises(pipe_module.GenerationExecutionError) as exc_info:
        pipe.process(PipeInput(input={"image": [_rgb_image(40, 24)]}), lambda o: None)
    return str(exc_info.value)


@pytest.mark.parametrize("at", ["load", "run"])
@pytest.mark.parametrize("sdk_message", [
    "NvVFX_Run failed: An invalid parameter value has been supplied for this effect+selector (code -7)",
    "NvVFX_Run failed: The specified image resolution is not supported (code -16)",
])
def test_a_refused_frame_size_is_explained(pipe_module, pipe_class, fake_nvvfx, no_real_cuda, at, sdk_message):
    message = _failing_process(pipe_module, pipe_class, fake_nvvfx, RuntimeError(sdk_message), at)

    assert "refused the frame size (40x24 to 80x48)" in message
    assert sdk_message in message


def test_an_uninitialized_effect_points_at_the_ngx_capability(pipe_module, pipe_class, fake_nvvfx, no_real_cuda):
    message = _failing_process(pipe_module, pipe_class, fake_nvvfx, RuntimeError(
        "NvVFX_Load failed: The effect has not been properly initialized (code -12)"))

    assert "could not initialize (40x24 to 80x48)" in message
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility,ngx" in message
    assert "refused" not in message


@pytest.mark.parametrize("code", [-17, -18, -19])
def test_an_unsupported_gpu_or_driver_names_what_the_effect_needs(pipe_module, pipe_class, fake_nvvfx, no_real_cuda, code):
    message = _failing_process(pipe_module, pipe_class, fake_nvvfx, RuntimeError(
        f"NvVFX_Load failed: The GPU is not supported (code {code})"))

    assert "does not run on this GPU or driver (40x24 to 80x48)" in message
    assert "Turing or newer" in message
    assert "570.190" in message


@pytest.mark.parametrize("sdk_message", [
    "NvVFX_Run failed: inference error",
    "NvVFX_Run failed: CUDA error (code -120)",
    "NvVFX_Run failed (code -70)",
])
def test_another_sdk_error_names_the_sizes(pipe_module, pipe_class, fake_nvvfx, no_real_cuda, sdk_message):
    message = _failing_process(pipe_module, pipe_class, fake_nvvfx, RuntimeError(sdk_message), "run")

    assert message == f"NVIDIA RTX Video Super Resolution failed (40x24 to 80x48): {sdk_message}"


def test_the_effect_is_closed_after_a_failure(pipe_module, pipe_class, fake_nvvfx, no_real_cuda):
    _failing_process(pipe_module, pipe_class, fake_nvvfx, RuntimeError("boom"), "run")

    assert fake_nvvfx.VideoSuperRes.instances[-1].closed


def test_frames_run_without_a_cuda_stream_when_cuda_is_absent(pipe_module, no_real_cuda):
    import contextlib
    import torch

    assert isinstance(pipe_module.default_stream(torch), contextlib.nullcontext)


def test_frames_run_on_the_default_cuda_stream(pipe_module):
    from types import SimpleNamespace

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True,
        default_stream=lambda: "stream 0",
        stream=lambda stream: ("on", stream),
    ))

    assert pipe_module.default_stream(fake_torch) == ("on", "stream 0")


def test_the_sdk_and_the_copies_run_inside_the_stream_context(pipe_module, fake_nvvfx, no_real_cuda, monkeypatch):
    import contextlib

    entered = []

    @contextlib.contextmanager
    def recording(torch_module):
        entered.append("in")
        yield
        entered.append("out")

    monkeypatch.setattr(pipe_module, "default_stream", recording)
    session = pipe_module._RtxSession(fake_nvvfx, fake_nvvfx.VideoSuperRes.QualityLevel.ULTRA)
    session.ensure_loaded(8, 8)
    effect = fake_nvvfx.VideoSuperRes.instances[-1]
    real_run = effect.run

    def run(frame):
        assert entered == ["in"]
        return real_run(frame)

    effect.run = run
    session.process_frame(_rgb_image(8, 8))

    assert entered == ["in", "out"]


def test_an_installed_nvvfx_that_cannot_load_says_so(pipe_module, blocking_finder_cls, monkeypatch):
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0.0" if name == "nvidia-vfx" else None)
    finder = blocking_finder_cls(["nvvfx"])
    monkeypatch.delitem(sys.modules, "nvvfx", raising=False)
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(pipe_module.GenerationExecutionError) as exc_info:
            pipe_module._import_nvvfx()
    finally:
        sys.meta_path.remove(finder)

    message = str(exc_info.value)
    assert message.startswith("NVIDIA RTX Video Super Resolution is installed (nvidia-vfx 0.2.0.0) but its library does not load: ")
    assert "nvvfx is blocked by the test harness" in message
    assert "--gpus all" in message
    assert "not installed" not in message


def test_a_missing_nvvfx_is_reported_not_installed(pipe_module, blocking_finder_cls, monkeypatch):
    import importlib.metadata

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    finder = blocking_finder_cls(["nvvfx"])
    monkeypatch.delitem(sys.modules, "nvvfx", raising=False)
    sys.meta_path.insert(0, finder)
    try:
        with pytest.raises(pipe_module.GenerationExecutionError) as exc_info:
            pipe_module._import_nvvfx()
    finally:
        sys.meta_path.remove(finder)

    assert str(exc_info.value).startswith("NVIDIA RTX Video Super Resolution is not installed. pip install")


def test_a_video_reaches_the_target_short_side(pipe_module, pipe_class, fake_nvvfx, no_real_cuda, tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2", reason="cv2 (or its native deps) not available", exc_type=ImportError)
    source = tmp_path / "in.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (40, 24))
    if not writer.isOpened():
        pytest.skip("no writable mp4 codec available for the synthetic input")
    for _ in range(3):
        writer.write(np.zeros((24, 40, 3), dtype=np.uint8))
    writer.release()
    opened = []

    class Recording:
        def __init__(self, path, width, height, fps):
            opened.append((width, height))
            self.frames = 0

        def write(self, frame):
            self.frames += 1

        def close(self):
            pass

    monkeypatch.setattr(pipe_module, "StreamingMp4Writer", Recording)
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 1.0, "target_short_side": 48, "keep_audio": False})

    pipe.process(PipeInput(input={"video": [str(source)]}), lambda o: None)

    assert opened == [(80, 48)]
    assert fake_nvvfx.VideoSuperRes.instances[-1].run_calls == 3


def test_an_effect_the_sdk_cannot_create_is_explained(pipe_module, pipe_class, fake_nvvfx, no_real_cuda, monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("NvVFX_CreateEffect failed: The GPU is not supported (code -17)")

    monkeypatch.setattr(fake_nvvfx, "VideoSuperRes", type("Refusing", (), {"QualityLevel": fake_nvvfx.VideoSuperRes.QualityLevel, "__init__": refuse}))
    pipe = pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0})

    with pytest.raises(pipe_module.GenerationExecutionError) as exc_info:
        pipe.process(PipeInput(input={"image": [_rgb_image(16, 16)]}), lambda o: None)

    assert str(exc_info.value).startswith("NVIDIA RTX Video Super Resolution does not run on this GPU or driver: it needs")


FRAMES = 3


def _write_input_video(path, frames=FRAMES, fps=10.0, size=(40, 24)):
    cv2 = pytest.importorskip("cv2", reason="cv2 (or its native deps) not available", exc_type=ImportError)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        pytest.skip("no writable mp4 codec available for the synthetic input")
    for _ in range(frames):
        writer.write(np.zeros((size[1], size[0], 3), dtype=np.uint8))
    writer.release()


class _RecordingWriter:
    instances = []

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


@pytest.fixture
def private_tempdir(tmp_path, monkeypatch):
    import tempfile

    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.fixture
def recording_writer(pipe_module, monkeypatch):
    _RecordingWriter.instances = []
    monkeypatch.setattr(pipe_module, "StreamingMp4Writer", _RecordingWriter)
    return _RecordingWriter


def _video_pipe(pipe_class, keep_audio=False):
    return pipe_class({"intent": "upscale", "quality": "ultra", "scale": 2.0, "keep_audio": keep_audio})


@pytest.mark.parametrize("calls", [0, 1])
def test_a_cancel_before_the_first_frame_is_a_cancel(
    tmp_path, pipe_class, fake_nvvfx, no_real_cuda, recording_writer, private_tempdir, calls,
):
    source = tmp_path / "in.mp4"
    _write_input_video(source)
    outputs = []

    with pytest.raises(SamplingCancelled):
        _video_pipe(pipe_class).process(
            PipeInput(input={"video": [str(source)]}), outputs.append, _CancelAfter(calls),
        )

    assert recording_writer.instances == []
    assert fake_nvvfx.VideoSuperRes.instances[-1].run_calls == 0
    assert fake_nvvfx.VideoSuperRes.instances[-1].closed
    assert _galleries(outputs) == []
    assert list(private_tempdir.iterdir()) == []


@pytest.mark.parametrize("calls", [2, 3])
def test_a_cancel_mid_clip_aborts_the_writer_and_publishes_nothing(
    tmp_path, pipe_class, fake_nvvfx, no_real_cuda, recording_writer, private_tempdir, calls,
):
    source = tmp_path / "in.mp4"
    _write_input_video(source)
    outputs = []

    with pytest.raises(SamplingCancelled):
        _video_pipe(pipe_class).process(
            PipeInput(input={"video": [str(source)]}), outputs.append, _CancelAfter(calls),
        )

    (writer,) = recording_writer.instances
    assert writer.calls == ["write"] * (calls - 1) + ["abort"]
    assert fake_nvvfx.VideoSuperRes.instances[-1].closed
    assert _galleries(outputs) == []
    assert list(private_tempdir.iterdir()) == []


def test_a_cancel_after_the_last_frame_never_closes_the_encoder(
    tmp_path, pipe_class, fake_nvvfx, no_real_cuda, recording_writer, private_tempdir,
):
    source = tmp_path / "in.mp4"
    _write_input_video(source)
    outputs = []

    with pytest.raises(SamplingCancelled):
        _video_pipe(pipe_class).process(
            PipeInput(input={"video": [str(source)]}), outputs.append, _CancelAfter(FRAMES + 2),
        )

    (writer,) = recording_writer.instances
    assert writer.calls == ["write"] * FRAMES + ["abort"]
    assert _galleries(outputs) == []
    assert list(private_tempdir.iterdir()) == []


def test_a_cancel_during_the_audio_mux_publishes_nothing(
    pipe_module, tmp_path, pipe_class, fake_nvvfx, no_real_cuda, recording_writer, private_tempdir, monkeypatch,
):
    source = tmp_path / "in.mp4"
    _write_input_video(source)
    muxed = []

    def mux_then_cancel(video_only, source_path, out_path):
        Path(out_path).write_bytes(Path(video_only).read_bytes() + b"audio")
        muxed.append(out_path)
        return True

    monkeypatch.setattr(pipe_module, "mux_audio_from_source", mux_then_cancel)
    outputs = []

    with pytest.raises(SamplingCancelled):
        _video_pipe(pipe_class, keep_audio=True).process(
            PipeInput(input={"video": [str(source)]}), outputs.append, lambda: bool(muxed),
        )

    assert len(muxed) == 1
    (writer,) = recording_writer.instances
    assert "close" in writer.calls
    assert _galleries(outputs) == []
    assert list(private_tempdir.iterdir()) == []


@pytest.mark.parametrize(
    "calls", [FRAMES + 4, FRAMES + 6], ids=["before_the_second_video", "inside_the_second_video"],
)
def test_a_cancel_on_the_second_video_drops_the_first_one_too(
    tmp_path, pipe_class, fake_nvvfx, no_real_cuda, recording_writer, private_tempdir, calls,
):
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _write_input_video(first)
    _write_input_video(second)
    outputs = []

    with pytest.raises(SamplingCancelled):
        _video_pipe(pipe_class).process(
            PipeInput(input={"video": [str(first), str(second)]}), outputs.append, _CancelAfter(calls),
        )

    assert recording_writer.instances[0].calls == ["write"] * FRAMES + ["close"]
    assert fake_nvvfx.VideoSuperRes.instances[-1].closed
    assert _galleries(outputs) == []
    assert list(private_tempdir.iterdir()) == []


def test_a_failure_mid_clip_aborts_the_writer_and_removes_the_files(
    pipe_module, tmp_path, pipe_class, fake_nvvfx, no_real_cuda, recording_writer, private_tempdir, monkeypatch,
):
    source = tmp_path / "in.mp4"
    _write_input_video(source)
    real_run = fake_nvvfx.VideoSuperRes.run

    def run_twice(self, frame):
        if self.run_calls == 2:
            raise RuntimeError("boom")
        return real_run(self, frame)

    monkeypatch.setattr(fake_nvvfx.VideoSuperRes, "run", run_twice)
    outputs = []

    with pytest.raises(pipe_module.GenerationExecutionError) as exc_info:
        _video_pipe(pipe_class).process(PipeInput(input={"video": [str(source)]}), outputs.append)

    assert str(exc_info.value) == "NVIDIA RTX Video Super Resolution failed (40x24 to 80x48): boom"
    (writer,) = recording_writer.instances
    assert writer.calls == ["write", "write", "abort"]
    assert _galleries(outputs) == []
    assert list(private_tempdir.iterdir()) == []


def test_a_failure_on_the_second_video_removes_the_first_one_too(
    pipe_module, tmp_path, pipe_class, fake_nvvfx, no_real_cuda, recording_writer, private_tempdir,
):
    first = tmp_path / "a.mp4"
    _write_input_video(first)
    missing = tmp_path / "missing.mp4"
    outputs = []

    with pytest.raises(pipe_module.GenerationExecutionError):
        _video_pipe(pipe_class).process(
            PipeInput(input={"video": [str(first), str(missing)]}), outputs.append,
        )

    assert recording_writer.instances[0].calls == ["write"] * FRAMES + ["close"]
    assert _galleries(outputs) == []
    assert list(private_tempdir.iterdir()) == []


def test_a_successful_mux_leaves_only_the_muxed_file(
    pipe_module, tmp_path, pipe_class, fake_nvvfx, no_real_cuda, recording_writer, private_tempdir, monkeypatch,
):
    source = tmp_path / "in.mp4"
    _write_input_video(source)

    def mux(video_only, source_path, out_path):
        Path(out_path).write_bytes(Path(video_only).read_bytes() + b"audio")
        return True

    monkeypatch.setattr(pipe_module, "mux_audio_from_source", mux)

    result = _video_pipe(pipe_class, keep_audio=True).process(
        PipeInput(input={"video": [str(source)]}), lambda o: None,
    )

    (path,) = result.output["video"]
    assert [p.name for p in private_tempdir.iterdir()] == [Path(path).name]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_a_cancel_mid_clip_with_the_real_encoder_leaves_nothing_behind(
    tmp_path, pipe_class, fake_nvvfx, no_real_cuda, private_tempdir,
):
    source = tmp_path / "in.mp4"
    _write_input_video(source)
    outputs = []

    with pytest.raises(SamplingCancelled):
        _video_pipe(pipe_class).process(
            PipeInput(input={"video": [str(source)]}), outputs.append, _CancelAfter(3),
        )

    assert fake_nvvfx.VideoSuperRes.instances[-1].run_calls == 2
    assert _galleries(outputs) == []
    assert list(private_tempdir.iterdir()) == []


def test_a_skipped_mux_leaves_only_the_video_only_file(
    pipe_module, tmp_path, pipe_class, fake_nvvfx, no_real_cuda, recording_writer, private_tempdir, monkeypatch,
):
    source = tmp_path / "in.mp4"
    _write_input_video(source)
    monkeypatch.setattr(pipe_module, "mux_audio_from_source", lambda video_only, source_path, out_path: False)

    result = _video_pipe(pipe_class, keep_audio=True).process(
        PipeInput(input={"video": [str(source)]}), lambda o: None,
    )

    (path,) = result.output["video"]
    assert [p.name for p in private_tempdir.iterdir()] == [Path(path).name]
