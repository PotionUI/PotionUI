from __future__ import annotations

import pytest
from PIL import Image

from src.platform.media_tools import (
    InvalidSelection,
    InvalidToolParams,
    ToolCancelled,
    ToolContext,
    ToolFailed,
    UnsupportedMedia,
    probe,
    run_chain,
)
from tests.platform.media_tools import fake_tools as fakes
from tests.platform.media_tools.media_factory import make_audio, make_image, make_video, run_ffmpeg

CROP = fakes.make_spec("crop", fakes.BoxCrop(), accepts=("image", "video"), params=fakes.BOX_PARAMS)
MIRROR = fakes.make_spec("mirror", fakes.Mirror(), accepts=("image", "video"))
TRIM = fakes.make_spec(
    "trim",
    fakes.Window(),
    accepts=("video", "audio"),
    params=[
        {"name": "start", "type": "float", "default": 0},
        {"name": "end", "type": "float"},
    ],
)
FASTER = fakes.make_spec(
    "faster",
    fakes.Faster(),
    accepts=("video", "audio"),
    params=[{"name": "factor", "type": "float", "default": 2}],
)
MUTE = fakes.make_spec("mute", fakes.Mute(), accepts=("video",))
QUALITY = fakes.make_spec(
    "quality", fakes.Quality(), accepts=("video",), params=[{"name": "crf", "type": "integer"}]
)
TINT = fakes.make_spec("tint", fakes.Tint(), accepts=("image",))
COPY = fakes.make_spec("copy", fakes.CopyOnly())
STACK = fakes.make_spec(
    "stack", fakes.Stack(), accepts=("video",), cardinality="many_to_one", selection={"min": 2, "max": 3}
)
FAN = fakes.make_spec(
    "fan",
    fakes.Fan(),
    accepts=("image",),
    cardinality="one_to_many",
    params=[{"name": "parts", "type": "integer", "default": 2}],
)
GRAB = fakes.make_spec("grab", fakes.FrameGrab(), accepts=("video",), produces="image")
HAS_SOUND = fakes.make_spec("sound", fakes.Mirror(), accepts=("video",), requires={"has_audio": True})
NEEDS_ALPHA = fakes.make_spec("alpha", fakes.Mirror(), accepts=("image",), requires={"has_alpha": True})
NEEDS_LENGTH = fakes.make_spec("long", fakes.Mirror(), accepts=("video",), requires={"min_duration": 5})
BROKEN = fakes.make_spec("broken", fakes.Broken())
COMPONENT_ONLY = fakes.make_spec("remote", None, component="plugin:p:Tool.js")


@pytest.fixture
def video(tmp_path):
    return probe(make_video(tmp_path / "clip.mp4", seconds=2.0, size=(64, 48), fps=10))


@pytest.fixture
def silent_video(tmp_path):
    return probe(make_video(tmp_path / "silent.mp4", seconds=1.0, audio=False))


@pytest.fixture
def photo(tmp_path):
    return probe(make_image(tmp_path / "photo.jpg", (40, 30)))


def scratch_files(ctx):
    return sorted(p.name for p in ctx.scratch_dir.glob("*")) if ctx.scratch_dir.exists() else []


def test_consecutive_video_fragments_run_in_one_ffmpeg_pass(video, ctx):
    out = run_chain(
        [(CROP, {"x": 0, "y": 0, "width": 32, "height": 24}), (MIRROR, {}), (MUTE, {})],
        [video],
        ctx,
    )

    assert len(ctx.commands) == 1
    command = ctx.commands[0]
    assert command[command.index("-vf") + 1] == "crop=32:24:0:0,hflip"
    assert "-an" in command
    assert (out[0].width, out[0].height) == (32, 24)
    assert out[0].has_audio is False


def test_leading_trim_becomes_an_input_seek_not_a_filter(video, ctx):
    out = run_chain([(TRIM, {"start": 0.5, "end": 1.5}), (MIRROR, {})], [video], ctx)

    command = ctx.commands[0]
    assert len(ctx.commands) == 1
    assert command[command.index("-ss") + 1] == "0.500000"
    assert command[command.index("-t") + 1] == "1.000000"
    assert command.index("-ss") < command.index("-i")
    assert "trim=" not in command[command.index("-vf") + 1]
    assert out[0].duration == pytest.approx(1.0, abs=0.2)
    assert out[0].has_audio is True


def test_trim_after_a_speed_change_becomes_an_inline_filter(video, ctx):
    out = run_chain([(FASTER, {"factor": 2}), (TRIM, {"start": 0.25, "end": 0.75})], [video], ctx)

    command = ctx.commands[0]
    assert len(ctx.commands) == 1
    assert "-ss" not in command
    video_filters = command[command.index("-vf") + 1]
    audio_filters = command[command.index("-af") + 1]
    assert video_filters.startswith("setpts=PTS/2.0,trim=start=0.250000:end=0.750000,setpts=PTS-STARTPTS")
    assert audio_filters.startswith("atempo=2.0,atrim=start=0.250000:end=0.750000,asetpts=PTS-STARTPTS")
    assert out[0].duration == pytest.approx(0.5, abs=0.2)


def test_two_leading_trims_compose_into_one_window(video, ctx):
    out = run_chain([(TRIM, {"start": 0.5, "end": 1.5}), (TRIM, {"start": 0.25, "end": 0.75})], [video], ctx)

    command = ctx.commands[0]
    assert len(ctx.commands) == 1
    assert command[command.index("-ss") + 1] == "0.750000"
    assert command[command.index("-t") + 1] == "0.500000"
    assert out[0].duration == pytest.approx(0.5, abs=0.2)


def test_a_step_without_a_fast_path_splits_the_chain(video, ctx):
    out = run_chain(
        [(MIRROR, {}), (COPY, {}), (CROP, {"x": 0, "y": 0, "width": 32, "height": 24})],
        [video],
        ctx,
    )

    assert len(ctx.commands) == 2
    assert (out[0].width, out[0].height) == (32, 24)


def test_second_step_is_validated_against_the_first_steps_output_size(video, ctx):
    with pytest.raises(InvalidToolParams) as raised:
        run_chain(
            [
                (CROP, {"x": 0, "y": 0, "width": 32, "height": 24}),
                (CROP, {"x": 20, "y": 0, "width": 20, "height": 10}),
            ],
            [video],
            ctx,
        )

    assert raised.value.field == "width"
    assert ctx.commands == []


def test_image_steps_run_in_memory_and_are_saved_once(photo, ctx):
    out = run_chain(
        [(CROP, {"x": 0, "y": 0, "width": 20, "height": 20}), (MIRROR, {}), (CROP, {"x": 0, "y": 0, "width": 10, "height": 8})],
        [photo],
        ctx,
    )

    assert ctx.commands == []
    assert len(scratch_files(ctx)) == 1
    assert (out[0].width, out[0].height) == (10, 8)
    assert out[0].path.parent == ctx.scratch_dir


def test_image_step_is_validated_against_the_previous_steps_size(photo, ctx):
    with pytest.raises(InvalidToolParams):
        run_chain(
            [(CROP, {"x": 0, "y": 0, "width": 20, "height": 20}), (CROP, {"x": 10, "y": 0, "width": 20, "height": 10})],
            [photo],
            ctx,
        )


def test_alpha_gained_by_a_step_turns_a_jpeg_into_a_png(photo, ctx):
    out = run_chain([(TINT, {})], [photo], ctx)

    assert out[0].path.suffix == ".png"
    assert out[0].has_alpha is True
    with Image.open(out[0].path) as saved:
        assert saved.mode == "RGBA"


def test_alpha_requirement_sees_the_alpha_the_previous_step_created(photo, ctx):
    out = run_chain([(TINT, {}), (NEEDS_ALPHA, {})], [photo], ctx)

    assert out[0].has_alpha is True
    assert len(scratch_files(ctx)) == 1


def test_alpha_requirement_refuses_an_opaque_image(photo, ctx):
    with pytest.raises(UnsupportedMedia):
        run_chain([(NEEDS_ALPHA, {})], [photo], ctx)


def test_sound_requirement_refuses_media_without_sound(silent_video, ctx):
    with pytest.raises(UnsupportedMedia):
        run_chain([(HAS_SOUND, {})], [silent_video], ctx)


def test_sound_requirement_sees_sound_dropped_by_the_previous_fused_step(video, ctx):
    with pytest.raises(UnsupportedMedia):
        run_chain([(MUTE, {}), (HAS_SOUND, {})], [video], ctx)


def test_duration_requirement_refuses_short_media(video, ctx):
    with pytest.raises(UnsupportedMedia):
        run_chain([(NEEDS_LENGTH, {})], [video], ctx)


def test_tool_refuses_a_kind_it_does_not_accept(tmp_path, ctx):
    audio = probe(make_audio(tmp_path / "tone.wav", 1))

    with pytest.raises(UnsupportedMedia):
        run_chain([(CROP, {"x": 0, "y": 0, "width": 1, "height": 1})], [audio], ctx)


def test_tool_without_an_implementation_cannot_run(photo, ctx):
    with pytest.raises(ToolFailed):
        run_chain([(COMPONENT_ONLY, {})], [photo], ctx)


def test_many_to_one_selection_bounds(tmp_path, ctx):
    clips = [probe(make_video(tmp_path / f"c{i}.mp4", 0.5)) for i in range(4)]

    with pytest.raises(InvalidSelection):
        run_chain([(STACK, {})], clips[:1], ctx)
    with pytest.raises(InvalidSelection):
        run_chain([(STACK, {})], clips, ctx)
    out = run_chain([(STACK, {})], clips[:2], ctx)

    assert len(out) == 1


def test_many_to_one_followed_by_a_fusible_step(tmp_path, ctx):
    clips = [probe(make_video(tmp_path / f"c{i}.mp4", 0.5, size=(64, 48))) for i in range(2)]

    out = run_chain([(STACK, {}), (CROP, {"x": 0, "y": 0, "width": 32, "height": 24})], clips, ctx)

    assert len(out) == 1
    assert (out[0].width, out[0].height) == (32, 24)


def test_one_to_many_applies_the_remaining_steps_to_every_part(photo, ctx):
    out = run_chain([(FAN, {"parts": 3}), (CROP, {"x": 0, "y": 0, "width": 10, "height": 10})], [photo], ctx)

    assert len(out) == 3
    assert {(m.width, m.height) for m in out} == {(10, 10)}
    assert len({m.path for m in out}) == 3


def test_a_one_to_one_step_that_returns_two_files_is_a_failure(photo, ctx):
    with pytest.raises(ToolFailed):
        run_chain([(BROKEN, {})], [photo], ctx)


def test_every_input_of_a_batch_goes_through_the_chain(tmp_path, ctx):
    images = [probe(make_image(tmp_path / f"i{n}.png", (40 + n, 30))) for n in range(3)]

    out = run_chain([(CROP, {"x": 0, "y": 0, "width": 20, "height": 10})], images, ctx)

    assert [(m.width, m.height) for m in out] == [(20, 10)] * 3
    assert len({m.path for m in out}) == 3


def test_mixed_image_and_video_batch(video, photo, ctx):
    out = run_chain([(CROP, {"x": 0, "y": 0, "width": 20, "height": 10}), (MIRROR, {})], [photo, video], ctx)

    assert [m.kind for m in out] == ["image", "video"]
    assert (out[1].width, out[1].height) == (20, 10)
    assert len(ctx.commands) == 1


def test_a_step_that_changes_kind_ends_the_fusion(video, ctx):
    out = run_chain([(GRAB, {}), (CROP, {"x": 0, "y": 0, "width": 20, "height": 10})], [video], ctx)

    assert out[0].kind == "image"
    assert (out[0].width, out[0].height) == (20, 10)
    assert len(ctx.commands) == 1


def test_odd_sized_video_still_encodes_to_even_dimensions(tmp_path, ctx):
    source = tmp_path / "odd.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=63x47:rate=10:duration=0.5",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv444p", str(source),
    )
    odd = probe(source)
    assert (odd.width, odd.height) == (63, 47)

    out = run_chain([(MIRROR, {})], [odd], ctx)

    assert (out[0].width, out[0].height) == (62, 46)


def test_even_sized_video_gets_no_extra_crop_filter(video, ctx):
    run_chain([(MIRROR, {})], [video], ctx)

    assert "trunc" not in ctx.commands[0][ctx.commands[0].index("-vf") + 1]


def test_last_fragment_quality_reaches_the_encoder(video, ctx):
    run_chain([(QUALITY, {"crf": 18}), (QUALITY, {"crf": 28})], [video], ctx)

    command = ctx.commands[0]
    assert command[command.index("-crf") + 1] == "28"


def test_default_quality_is_balanced(video, ctx):
    run_chain([(MIRROR, {})], [video], ctx)

    command = ctx.commands[0]
    assert command[command.index("-crf") + 1] == "23"


def test_audio_steps_fuse_into_one_pass(tmp_path, ctx):
    audio = probe(make_audio(tmp_path / "tone.wav", 2.0))

    out = run_chain([(TRIM, {"start": 0.5, "end": 1.5}), (FASTER, {"factor": 2})], [audio], ctx)

    assert len(ctx.commands) == 1
    assert "-vn" in ctx.commands[0]
    assert out[0].kind == "audio"
    assert out[0].duration == pytest.approx(0.5, abs=0.15)


def test_virtual_media_is_what_the_next_fragment_sees(video, ctx):
    recorder = fakes.Recording()
    spy = fakes.make_spec("spy", recorder, accepts=("video",))

    run_chain([(CROP, {"x": 0, "y": 0, "width": 32, "height": 24}), (spy, {})], [video], ctx)

    assert (recorder.seen[0].width, recorder.seen[0].height) == (32, 24)


def test_invalid_params_report_the_step_index(photo, ctx):
    with pytest.raises(InvalidToolParams) as raised:
        run_chain([(MIRROR, {}), (CROP, {"x": 0, "y": 0, "width": 0, "height": 5})], [photo], ctx)

    assert raised.value.field == "width"
    assert raised.value.step_index == 1


def test_params_are_validated_unless_the_caller_opts_out(photo, ctx):
    with pytest.raises(InvalidToolParams):
        run_chain([(CROP, {"x": 0, "y": 0, "width": 5})], [photo], ctx)


def test_empty_steps_and_empty_inputs_are_refused(photo, ctx):
    with pytest.raises(ToolFailed):
        run_chain([], [photo], ctx)
    with pytest.raises(InvalidSelection):
        run_chain([(MIRROR, {})], [], ctx)


def test_cancelled_context_stops_before_any_work(video, tmp_path):
    cancelled = ToolContext(tmp_path / "scratch", is_cancelled=lambda: True)

    with pytest.raises(ToolCancelled):
        run_chain([(MIRROR, {})], [video], cancelled)

    assert cancelled.commands == []
    assert scratch_files(cancelled) == []


def test_cancel_between_stages_skips_the_rest(video, tmp_path):
    calls = {"n": 0}

    def cancel_after_first_stage():
        calls["n"] += 1
        return len([c for c in contextual.commands]) >= 1

    contextual = ToolContext(tmp_path / "scratch", is_cancelled=cancel_after_first_stage)

    with pytest.raises(ToolCancelled):
        run_chain([(MIRROR, {}), (COPY, {}), (MIRROR, {})], [video], contextual)

    assert len(contextual.commands) == 1


def test_progress_is_monotonic_and_finishes_at_one(video, tmp_path):
    seen = []
    tracked = ToolContext(tmp_path / "scratch", progress=lambda fraction, label: seen.append((fraction, label)))

    run_chain([(MIRROR, {}), (COPY, {}), (CROP, {"x": 0, "y": 0, "width": 32, "height": 24})], [video], tracked)

    fractions = [fraction for fraction, _ in seen]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    assert all(0.0 <= fraction <= 1.0 for fraction in fractions)
    assert {"Mirror", "Copy", "Crop"} <= {label for _, label in seen}


class _Summarizes:
    def __init__(self, summarize=True):
        self.summarize = summarize

    def run(self, inputs, params, ctx):
        if self.summarize:
            ctx.summarize(f"Saved {len(inputs)} file(s) elsewhere", [("Open", "/models?id=m-1")])
        return []


def _summary_spec(tool_id, impl, cardinality):
    from src.platform.media_tools import MediaTool

    class Tool(MediaTool):
        def run(self, inputs, params, ctx):
            return impl.run(inputs, params, ctx)

    return fakes.make_spec(tool_id, Tool(), accepts=("image",), cardinality=cardinality, selection={"min": 1})


@pytest.mark.parametrize("cardinality", ["many_to_one", "one_to_one"])
def test_a_last_step_that_summarises_may_finish_without_media(tmp_path, cardinality):
    ctx = ToolContext(tmp_path / "s")
    images = [probe(make_image(tmp_path / f"i{n}.png", (40, 30))) for n in range(2)]
    spec = _summary_spec("keep", _Summarizes(), cardinality)
    assert run_chain([(spec, {})], images, ctx) == []
    assert ctx.summary.message.startswith("Saved")
    assert [(link.label, link.href) for link in ctx.summary.links] == [("Open", "/models?id=m-1")]


def test_a_step_with_no_media_and_no_summary_still_fails(tmp_path):
    ctx = ToolContext(tmp_path / "s")
    image = probe(make_image(tmp_path / "i.png", (40, 30)))
    with pytest.raises(ToolFailed, match="exactly one result"):
        run_chain([(_summary_spec("keep", _Summarizes(False), "many_to_one"), {})], [image], ctx)


def test_a_summary_cannot_end_the_chain_early(tmp_path):
    ctx = ToolContext(tmp_path / "s")
    image = probe(make_image(tmp_path / "i.png", (40, 30)))
    with pytest.raises(ToolFailed, match="exactly one result"):
        run_chain([(_summary_spec("keep", _Summarizes(), "many_to_one"), {}), (TINT, {})], [image], ctx)
