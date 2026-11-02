from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.platform.media_tools import ToolContext, ToolFailed, ToolMedia, UnsupportedMedia
from src.platform.media_tools.effects.loader import load_effect
from src.platform.media_tools.effects.runtime import EffectRuntime, LifecycleGpuPolicy, parse_filters
from src.platform.media_tools.effects.tool import ANALYSIS_KEY, EffectTool
from src.platform.media_tools.timeline import Clock, StepContext
from tests.platform.media_tools.effects.builder import effect_data, write_effect
from tests.platform.media_tools.ops.raw_runner import RawRunner

FFMPEG_FIRST = [
    {"ffmpeg": {"vf": [{"colorlevels": {"rimax": "{hi}", "gimax": "{hi}", "bimax": "{hi}"}}]}},
    {"torch": "render.py:apply_torch"},
    {"numpy": "render.py:apply"},
]
SIZED = {"k": "strength / 100", "hi": "1 - k", "cell": "max(1, floor(min(media.width, media.height) / 10))"}
ANALYSED = {"python": "analyze.py:measure", "keys": ["strength"], "values": ["level"]}
MEASURE = (
    "CALLS = []\n\n\n"
    "def measure(ctx, params):\n"
    "    CALLS.append(params['strength'])\n"
    "    frames = ctx.sample_frames(width=8, height=6, rate=2.0, max_frames=10)\n"
    "    return {'level': float(frames.mean()) / 255}\n"
)
VIDEO = ToolMedia(path=Path("clip.mp4"), kind="video", width=64, height=36, duration=2.0, fps=24.0)


def runtime(filters=frozenset({"colorlevels"}), torch=True, gpu=lambda: False):
    return EffectRuntime(filters=lambda: filters, torch_available=lambda: torch, gpu_policy=gpu)


def tool(tmp_path, data=None, files=None, effect_runtime=None):
    folder = write_effect(tmp_path, data=data or effect_data(render=FFMPEG_FIRST, derive=SIZED), files=files)
    return EffectTool(load_effect(folder, "core"), effect_runtime or runtime())


def test_the_first_available_candidate_wins_in_listed_order(tmp_path):
    effect = tool(tmp_path).effect

    assert runtime().choose(effect).candidate.kind == "ffmpeg"
    assert runtime(filters=frozenset()).choose(effect).candidate.kind == "torch"
    assert runtime(filters=frozenset(), torch=False).choose(effect).candidate.kind == "numpy"
    assert runtime().choose(effect, python_only=True).candidate.kind == "torch"


def test_torch_runs_on_cuda_only_when_the_policy_allows(tmp_path):
    effect = tool(tmp_path).effect

    assert runtime(filters=frozenset(), gpu=lambda: True).choose(effect).device == "cuda"
    assert runtime(filters=frozenset()).choose(effect).device == "cpu"

    def broken():
        raise RuntimeError("nvml")

    assert runtime(filters=frozenset(), gpu=broken).choose(effect).device == "cpu"


class FakeMonitor:
    def __init__(self, free):
        self.free = free

    def get_free_vram(self):
        return self.free


class FakeLifecycle:
    def __init__(self, leases=0, free=4096):
        self.leases = leases
        self.gpu_monitor = FakeMonitor(free)

    def stats(self):
        return {"active_leases": self.leases}


@pytest.mark.parametrize(
    "cuda, leases, free, allowed",
    [(True, 0, 4096, True), (True, 1, 4096, False), (True, 0, 512, False), (True, 0, None, False), (False, 0, 4096, False)],
)
def test_the_lifecycle_policy_needs_cuda_a_free_gigabyte_and_no_generation(monkeypatch, cuda, leases, free, allowed):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)

    assert LifecycleGpuPolicy(FakeLifecycle(leases, free))() is allowed


def test_the_ffmpeg_filter_listing_is_parsed():
    text = (
        "Filters:\n  T.. = Timeline support\n  A = Audio input/output\n"
        " TSC colorlevels       V->V       Adjust the color levels.\n"
        " ... amovie            |->N       Read audio from a movie source.\n"
        " T.. vignette          V->V       Make or reverse a vignette effect.\n"
    )

    assert parse_filters(text) == frozenset({"colorlevels", "amovie", "vignette"})


def test_an_ffmpeg_candidate_compiles_into_an_rgb_fragment(tmp_path):
    fragment = tool(tmp_path).video_filter(VIDEO, {"strength": 25})

    assert fragment.vf == ["colorlevels=rimax='0.75':gimax='0.75':bimax='0.75'"]
    assert fragment.rgb and fragment.stage is None


def test_a_python_candidate_becomes_a_frame_stage_with_params_and_derived_values(tmp_path):
    fragment = tool(tmp_path, effect_runtime=runtime(filters=frozenset(), torch=False)).video_filter(VIDEO, {"strength": 25})

    stage = fragment.stage
    assert fragment.vf == [] and stage.kind == "numpy"
    assert stage.params == {"strength": 25} and stage.derived["cell"] == 3
    frames = np.full((2, 4, 4, 3), 200, dtype=np.uint8)
    assert stage.apply(frames, (0, 1), 24.0, 2.0).max() == 150


def test_torch_and_numpy_stages_agree_to_the_level(tmp_path):
    numpy_stage = tool(tmp_path, effect_runtime=runtime(filters=frozenset(), torch=False)).video_filter(VIDEO, {"strength": 33}).stage
    torch_stage = tool(tmp_path, effect_runtime=runtime(filters=frozenset())).video_filter(VIDEO, {"strength": 33}).stage
    frames = np.random.default_rng(3).integers(0, 256, size=(3, 9, 7, 3), dtype=np.uint8)

    out_numpy = numpy_stage.apply(frames, (0, 1, 2), 24.0, 2.0)
    out_torch = torch_stage.apply(frames, (0, 1, 2), 24.0, 2.0)

    assert torch_stage.kind == "torch" and out_torch.dtype == np.uint8
    difference = np.abs(out_numpy.astype(int) - out_torch.astype(int))
    assert difference.max() <= 1 and difference.mean() <= 0.02


def test_no_available_candidate_is_a_tool_failure(tmp_path):
    data = effect_data(render=[FFMPEG_FIRST[0]], derive=SIZED)

    with pytest.raises(ToolFailed, match="cannot render"):
        tool(tmp_path, data=data, effect_runtime=runtime(filters=frozenset())).video_filter(VIDEO, {"strength": 25})


def test_a_derive_that_fails_for_these_params_is_a_tool_failure(tmp_path):
    data = effect_data(derive={"k": "1 / (strength - 50)"})

    with pytest.raises(ToolFailed, match="division by zero"):
        tool(tmp_path, data=data).video_filter(VIDEO, {"strength": 50})


def analysed(tmp_path, effect_runtime=None):
    data = effect_data(analysis=ANALYSED, derive={"k": "level * strength / 100"})
    return tool(tmp_path, data=data, files={"analyze.py": MEASURE}, effect_runtime=effect_runtime)


def step_context(tmp_path, frames, clock=Clock(length=2.0), memo=None):
    def prober(path, kind=None):
        return VIDEO

    ctx = ToolContext(tmp_path / "scratch", runner=RawRunner(frames), prober=prober)
    kwargs = {"memo": memo} if memo else {}
    return StepContext(source=VIDEO, clock=clock, ctx=ctx, **kwargs), ctx


def test_analysis_runs_before_the_fragment_and_feeds_the_derive(tmp_path):
    effect_tool = analysed(tmp_path, runtime(filters=frozenset(), torch=False))
    step, _ = step_context(tmp_path, np.full((4, 6, 8, 3), 51, dtype=np.uint8))

    prepared = effect_tool.prepare({"strength": 50}, step)

    assert prepared[ANALYSIS_KEY] == {"level": pytest.approx(0.2)}
    stage = effect_tool.video_filter(VIDEO, prepared).stage
    assert stage.derived == {"level": pytest.approx(0.2), "k": pytest.approx(0.1)}
    assert stage.params == {"strength": 50}
    with pytest.raises(ToolFailed, match="not measured"):
        effect_tool.video_filter(VIDEO, {"strength": 50})


def test_analysis_samples_only_the_window_the_step_sees_at_a_capped_rate(tmp_path):
    effect_tool = analysed(tmp_path)
    clock = Clock(length=10.0).trimmed(2.0, 6.0).sped(2.0)
    step, ctx = step_context(tmp_path, np.zeros((2, 6, 8, 3), dtype=np.uint8), clock=clock)

    effect_tool.prepare({"strength": 50}, step)

    (decode,) = ctx.runner.decodes()
    assert decode[decode.index("-ss") + 1] == "2.000000" and decode[decode.index("-t") + 1] == "4.000000"
    assert "fps=1.000000,scale=8:6:flags=area" in decode and "-an" in decode
    assert decode[decode.index("-frames:v") + 1] == "10"
    assert not list((tmp_path / "scratch").glob("frames-*.rgb"))


def test_a_long_window_lowers_the_rate_so_the_samples_fit(tmp_path):
    effect_tool = analysed(tmp_path)
    step, ctx = step_context(tmp_path, np.zeros((2, 6, 8, 3), dtype=np.uint8), clock=Clock(length=40.0))

    effect_tool.prepare({"strength": 50}, step)

    (decode,) = ctx.runner.decodes()
    assert "fps=0.250000,scale=8:6:flags=area" in decode


def test_analysis_is_memoised_by_window_and_keys(tmp_path):
    effect_tool = analysed(tmp_path)
    store = {}

    def memo(key, compute):
        if key not in store:
            store[key] = compute()
        return store[key]

    frames = np.zeros((2, 6, 8, 3), dtype=np.uint8)
    for strength in (50, 50, 60):
        step, _ = step_context(tmp_path, frames, memo=memo)
        effect_tool.prepare({"strength": strength}, step)

    module_calls = effect_tool.effect.analysis.function.__globals__["CALLS"]
    assert module_calls == [50, 60] and len(store) == 2


@pytest.mark.parametrize(
    "body",
    ["return {'other': 1.0}", "return {'level': float('nan')}", "return {'level': 'bright'}", "return [1.0]"],
)
def test_bad_analysis_results_are_tool_failures(tmp_path, body):
    files = {"analyze.py": f"def measure(ctx, params):\n    {body}\n"}
    data = effect_data(analysis=ANALYSED, derive={"k": "level * strength / 100"})
    effect_tool = tool(tmp_path, data=data, files=files)
    step, _ = step_context(tmp_path, np.zeros((1, 6, 8, 3), dtype=np.uint8))

    with pytest.raises(ToolFailed):
        effect_tool.prepare({"strength": 50}, step)


@pytest.mark.parametrize(
    "request_args",
    [
        "width=0, height=6, rate=2.0, max_frames=10",
        "width=2048, height=6, rate=2.0, max_frames=10",
        "width=8, height=6, rate=float('inf'), max_frames=10",
        "width=8, height=6, rate=-1.0, max_frames=10",
        "width=8, height=6, rate=2.0, max_frames=0",
    ],
)
def test_analysis_frame_requests_are_bounded(tmp_path, request_args):
    files = {"analyze.py": f"def measure(ctx, params):\n    ctx.sample_frames({request_args})\n"}
    data = effect_data(analysis=ANALYSED, derive={"k": "level * strength / 100"})
    effect_tool = tool(tmp_path, data=data, files=files)
    step, _ = step_context(tmp_path, np.zeros((1, 6, 8, 3), dtype=np.uint8))

    with pytest.raises(ToolFailed):
        effect_tool.prepare({"strength": 50}, step)


def test_an_image_runs_through_the_python_candidate_and_keeps_its_alpha(tmp_path):
    effect_tool = tool(tmp_path)
    image = Image.new("RGBA", (6, 4), (200, 100, 50, 77))

    result = effect_tool.image(image, {"strength": 50})

    assert result.mode == "RGBA" and result.getpixel((0, 0)) == (100, 50, 25, 77)


def test_only_effects_with_a_python_candidate_and_no_analysis_edit_images(tmp_path):
    assert tool(tmp_path / "a").has_image_path()
    assert not tool(tmp_path / "b", data=effect_data(render=[FFMPEG_FIRST[0]], derive=SIZED)).has_image_path()
    assert not analysed(tmp_path / "c").has_image_path()
    with pytest.raises(UnsupportedMedia):
        analysed(tmp_path / "d").image(Image.new("RGB", (4, 4)), {"strength": 50})
