from __future__ import annotations

import numpy as np
import pytest

from src.platform.media_tools import InvalidToolParams, ToolFailed, run_chain
from src.platform.media_tools.ops import cut_finder
from tests.platform.media_tools.ops.raw_runner import raw_context, video_media


def shot(count, seed, base):
    rng = np.random.default_rng(seed)
    noise = rng.integers(-25, 26, size=(count, 36, 64, 3))
    return np.clip(np.array(base) + noise, 0, 255).astype(np.uint8)


def two_cut_clip():
    return np.concatenate([shot(24, 1, (200, 60, 50)), shot(24, 2, (40, 90, 210)), shot(24, 3, (30, 180, 60))])


def run_cuts(tmp_path, frames, **params):
    ctx = raw_context(tmp_path, frames)
    media = video_media(tmp_path, duration=len(frames) / 24.0)
    values = {"sensitivity": 50, "min_shot_seconds": 0.5, **params}
    return run_chain([(cut_finder.spec(), values)], [media], ctx), ctx.runner


def trim_window(command):
    start = float(command[command.index("-ss") + 1]) if "-ss" in command else 0.0
    return start, start + float(command[command.index("-t") + 1])


def test_spec_is_a_one_to_many_video_tool():
    spec = cut_finder.spec()

    assert spec.cardinality == "one_to_many" and spec.accepts == ("video",) and spec.produces == "video"


def test_a_clip_with_two_hard_cuts_is_saved_as_three_shots(tmp_path):
    shots, runner = run_cuts(tmp_path, two_cut_clip())

    assert len(shots) == 3
    assert len(runner.encodes()) == 3


def test_shot_windows_meet_at_the_half_frame_before_each_cut(tmp_path):
    _, runner = run_cuts(tmp_path, two_cut_clip())

    windows = [trim_window(command) for command in runner.encodes()]
    assert windows[0][0] == 0.0
    assert windows[0][1] == pytest.approx(23.5 / 24.0)
    assert windows[1] == (pytest.approx(windows[0][1]), pytest.approx(47.5 / 24.0))
    assert windows[2][0] == pytest.approx(windows[1][1])
    assert windows[2][1] == pytest.approx(3.0)


def test_analysis_decodes_at_the_source_rate_in_one_pass(tmp_path):
    _, runner = run_cuts(tmp_path, two_cut_clip())

    assert len(runner.decodes()) == 1
    assert "fps=24.000000,scale=64:36:flags=area" in runner.decodes()[0]
    assert not list((tmp_path / "scratch").glob("frames-*.rgb"))


def test_a_clip_without_cuts_says_so(tmp_path):
    ramp = np.concatenate([np.full((1, 36, 64, 3), v, dtype=np.uint8) for v in np.linspace(20, 230, 72)])

    with pytest.raises(ToolFailed, match="No cuts"):
        run_cuts(tmp_path, ramp)


def test_too_many_shots_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cut_finder, "MAX_SHOTS", 2)

    with pytest.raises(InvalidToolParams, match="lower the sensitivity"):
        run_cuts(tmp_path, two_cut_clip())


def test_shot_boundaries_cover_the_whole_duration():
    windows = cut_finder.shot_boundaries([24, 48], 24.0, 3.0)

    assert windows[0][0] == 0.0 and windows[-1][1] == 3.0
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))
