from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.media_tools import MediaToolRegistry, ToolContext, ToolMedia, ToolSpec
from src.platform.media_tools.types import FfmpegUnavailable, InvalidToolParams, MediaToolError, ToolCancelled


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.calls = []

    def run(self, args, *, duration=None, on_progress=None, is_cancelled=None):
        self.commands.append(list(args))
        self.calls.append((duration, on_progress, is_cancelled))
        if on_progress:
            on_progress(0.5, "")


def test_progress_is_clamped_and_forwarded(tmp_path):
    seen = []
    context = ToolContext(tmp_path, progress=lambda fraction, label: seen.append((fraction, label)))

    context.progress(-1)
    context.progress(0.25, "working")
    context.progress(7)

    assert seen == [(0.0, ""), (0.25, "working"), (1.0, "")]


def test_progress_without_a_callback_is_a_no_op(tmp_path):
    ToolContext(tmp_path).progress(0.5, "x")


def test_scoped_contexts_map_progress_into_their_window(tmp_path):
    seen = []
    context = ToolContext(tmp_path, progress=lambda fraction, label: seen.append(fraction))

    inner = context.scoped(0.5, 1.0)
    inner.progress(0.0)
    inner.progress(0.5)
    inner.progress(1.0)
    inner.scoped(0.5, 1.0).progress(0.0)

    assert seen == [0.5, 0.75, 1.0, 0.75]


def test_scoped_context_shares_runner_counter_scratch_and_cancellation(tmp_path):
    runner = FakeRunner()
    flag = {"cancelled": False}
    context = ToolContext(tmp_path / "s", runner=runner, is_cancelled=lambda: flag["cancelled"])
    inner = context.scoped(0.0, 0.5)

    first = context.new_path(".png")
    second = inner.new_path(".png")
    inner.ffmpeg(["-i", "x"])

    assert first != second
    assert second.parent == tmp_path / "s"
    assert context.commands is inner.commands is runner.commands
    assert not inner.is_cancelled()
    flag["cancelled"] = True
    assert inner.is_cancelled() and context.is_cancelled()


def test_new_path_is_unique_lowercased_and_creates_the_scratch_dir(tmp_path):
    context = ToolContext(tmp_path / "deep" / "scratch")

    paths = [context.new_path(".PNG", stem="frame") for _ in range(3)]

    assert len(set(paths)) == 3
    assert all(p.suffix == ".png" and p.name.startswith("frame-") for p in paths)
    assert (tmp_path / "deep" / "scratch").is_dir()


def test_ffmpeg_passes_duration_progress_and_cancel_to_the_runner(tmp_path):
    runner = FakeRunner()
    seen = []
    context = ToolContext(tmp_path, runner=runner, progress=lambda fraction, label: seen.append(fraction))

    context.ffmpeg(["-i", "a"], duration=4.0)

    duration, on_progress, is_cancelled = runner.calls[0]
    assert duration == 4.0
    assert seen == [0.5]
    assert is_cancelled() is False


def test_probe_uses_the_injected_prober_and_forwards_the_kind(tmp_path):
    calls = []

    def prober(path, kind=None):
        calls.append((path, kind))
        return ToolMedia(path=path, kind=kind or "image")

    context = ToolContext(tmp_path, prober=prober)

    assert context.probe("a.png").kind == "image"
    assert context.probe(Path("b.mp4"), "video").kind == "video"
    assert calls == [(Path("a.png"), None), (Path("b.mp4"), "video")]


def test_probe_accepts_a_one_argument_prober(tmp_path):
    context = ToolContext(tmp_path, prober=lambda path: ToolMedia(path=path, kind="audio"))

    assert context.probe("a.wav").kind == "audio"


def test_tool_media_is_frozen_and_with_changes_copies():
    media = ToolMedia(path=Path("a.png"), kind="image", width=1)

    changed = media.with_changes(width=2)

    assert (media.width, changed.width) == (1, 2)
    with pytest.raises(Exception):
        media.width = 3


def test_error_codes_and_fields():
    params = InvalidToolParams("end", "bad", step_index=2)

    assert (params.code, params.field, params.message, params.step_index) == ("invalid_params", "end", "bad", 2)
    assert isinstance(params, ValueError) and isinstance(params, MediaToolError)
    assert ToolCancelled().code == "cancelled"
    assert FfmpegUnavailable("x").code == "ffmpeg_unavailable"
    assert str(params) == "bad"


def test_default_scopes_and_unknown_defaults():
    spec = ToolSpec(id="x", label="X")

    assert spec.scopes == ("library", "history", "field")
    assert spec.source == "core" and spec.editor is None and spec.preview is False
    assert MediaToolRegistry().list() == []


def test_a_context_has_no_user_unless_given_one(tmp_path):
    assert ToolContext(tmp_path).user_id is None


def test_scoped_contexts_keep_the_user_id(tmp_path):
    context = ToolContext(tmp_path, user_id="user-7")
    assert context.scoped(0.2, 0.4).scoped(0.0, 0.5).user_id == "user-7"
