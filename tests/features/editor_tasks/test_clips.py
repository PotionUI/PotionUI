import pytest

from src.features.editor_tasks import clips
from src.platform.http.errors import DomainError
from tests.features.editor_tasks.clip_support import count_frames, frame_digests, lossless_clip
from tests.platform.media_tools.media_factory import ffmpeg_available, run_ffmpeg

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not available")


@pytest.fixture(autouse=True)
def lossless(monkeypatch):
    monkeypatch.setattr(clips, "CRF", 0)


@pytest.fixture
def clip(tmp_path):
    return lossless_clip(tmp_path / "clip.mp4", frames=20, fps=10)


def facts(path):
    return clips.facts_of(path)


def test_facts_read_size_rate_and_frames(clip):
    found = facts(clip)

    assert (found.width, found.height, found.fps, found.frames, found.has_audio) == (64, 48, 10.0, 20, True)


@pytest.mark.parametrize(
    "start, end, expected",
    [(None, None, (0, 20)), (0.5, 1.0, (5, 10)), (0.0, 2.0, (0, 20)), (1.04, 1.46, (10, 15)), (1.5, 2.04, (15, 20))],
)
def test_the_range_lands_on_frames_at_the_clips_rate(clip, start, end, expected):
    window = clips.plan_range(facts(clip), start, end)

    assert (window.start, window.end) == expected


@pytest.mark.parametrize(
    "start, end, code, words",
    [
        (1.0, 1.0, "range_invalid", "end after it starts"),
        (1.5, 1.0, "range_invalid", "end after it starts"),
        (-0.5, 1.0, "range_invalid", "before the beginning"),
        (2.5, 3.0, "range_outside_clip", "starts (2.5s) past the end"),
        (0.5, 3.0, "range_outside_clip", "ends (3s) past the end"),
        (1.0, 1.02, "range_invalid", "shorter than one frame"),
    ],
)
def test_a_range_that_does_not_fit_the_clip_is_refused_with_its_reason(clip, start, end, code, words):
    with pytest.raises(DomainError) as refused:
        clips.plan_range(facts(clip), start, end)

    assert refused.value.status_code == 422
    assert refused.value.code == code
    assert words in refused.value.message


def test_cut_has_exactly_the_frames_of_the_range(clip, tmp_path):
    found = facts(clip)
    window = clips.plan_range(found, 0.5, 1.2)

    cut = clips.cut(clip, found, window, tmp_path / "cut.mp4")

    assert frame_digests(cut) == frame_digests(clip)[5:12]


def test_conform_brings_size_rate_and_length_to_the_clip(clip, tmp_path):
    found = facts(clip)
    other = tmp_path / "other.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=128x96:rate=24", "-frames:v", "30", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(other),
    )

    short = clips.conform(other, found, tmp_path / "short.mp4", count=7)
    long = clips.conform(other, found, tmp_path / "long.mp4", count=50)

    assert count_frames(short) == 7
    assert count_frames(long) == 50
    assert facts(short).width == 64 and facts(short).height == 48
    assert facts(short).fps == 10.0


def test_conform_crops_a_different_shape_to_fill_the_frame(clip, tmp_path):
    found = facts(clip)
    wide = tmp_path / "wide.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=160x48:rate=10", "-frames:v", "5", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(wide),
    )

    out = clips.conform(wide, found, tmp_path / "out.mp4", count=5)

    assert (facts(out).width, facts(out).height) == (64, 48)


def test_conform_can_adopt_the_results_own_size(clip, tmp_path):
    found = facts(clip)
    big = tmp_path / "big.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=128x96:rate=10", "-frames:v", "20", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(big),
    )

    out = clips.conform(big, found, tmp_path / "out.mp4", count=20, adopt_size=True)

    assert (facts(out).width, facts(out).height) == (128, 96)


def test_replace_leaves_every_frame_outside_the_range_untouched(clip, tmp_path):
    found = facts(clip)
    window = clips.plan_range(found, 0.5, 1.0)
    patch = lossless_clip(tmp_path / "patch.mp4", frames=5, fps=10, audio=False, pattern="mandelbrot")

    out = clips.splice_replace(clip, found, window, patch, tmp_path / "out.mp4")

    original, replaced = frame_digests(clip), frame_digests(out)
    assert len(replaced) == 20
    assert replaced[:5] == original[:5]
    assert replaced[10:] == original[10:]
    assert replaced[5:10] == frame_digests(patch)[:5]
    assert replaced[5:10] != original[5:10]


@pytest.mark.parametrize("start, end", [(0.0, 0.5), (1.5, 2.0), (0.0, 2.0)])
def test_replace_at_the_edges_keeps_the_length(clip, tmp_path, start, end):
    found = facts(clip)
    window = clips.plan_range(found, start, end)
    patch = lossless_clip(tmp_path / "patch.mp4", frames=window.count, fps=10, audio=False, pattern="mandelbrot")

    out = clips.splice_replace(clip, found, window, patch, tmp_path / "out.mp4")

    assert count_frames(out) == 20


def test_replace_keeps_the_original_sound(clip, tmp_path):
    found = facts(clip)
    window = clips.plan_range(found, 0.5, 1.0)
    patch = lossless_clip(tmp_path / "patch.mp4", frames=5, fps=10, audio=False)

    out = clips.splice_replace(clip, found, window, patch, tmp_path / "out.mp4")

    assert facts(out).has_audio
    assert abs(facts(out).duration - found.duration) < 0.15


def test_replace_of_a_silent_clip_stays_silent(tmp_path):
    silent = lossless_clip(tmp_path / "silent.mp4", frames=10, audio=False)
    found = facts(silent)
    window = clips.plan_range(found, 0.2, 0.5)
    patch = lossless_clip(tmp_path / "patch.mp4", frames=3, audio=False, pattern="mandelbrot")

    out = clips.splice_replace(silent, found, window, patch, tmp_path / "out.mp4")

    assert not facts(out).has_audio


def test_append_inserts_the_new_frames_at_the_point_and_keeps_the_rest(clip, tmp_path):
    found = facts(clip)
    added = lossless_clip(tmp_path / "added.mp4", frames=8, fps=10, audio=False, pattern="mandelbrot")

    out = clips.splice_append(clip, found, 10, added, 8, False, tmp_path / "out.mp4")

    original, joined = frame_digests(clip), frame_digests(out)
    assert len(joined) == 28
    assert joined[:10] == original[:10]
    assert joined[10:18] == frame_digests(added)
    assert joined[18:] == original[10:]


def test_append_at_the_end_adds_time_after_the_clip(clip, tmp_path):
    found = facts(clip)
    added = lossless_clip(tmp_path / "added.mp4", frames=5, fps=10, audio=False, pattern="mandelbrot")

    out = clips.splice_append(clip, found, 20, added, 5, False, tmp_path / "out.mp4")

    assert frame_digests(out)[:20] == frame_digests(clip)
    assert count_frames(out) == 25


def test_append_pads_the_added_part_with_silence_when_the_result_has_no_sound(clip, tmp_path):
    found = facts(clip)
    added = lossless_clip(tmp_path / "added.mp4", frames=10, fps=10, audio=False, pattern="mandelbrot")

    out = clips.splice_append(clip, found, 20, added, 10, False, tmp_path / "out.mp4")

    assert facts(out).has_audio
    assert abs(facts(out).duration - 3.0) < 0.2


def test_append_takes_the_added_sound_from_the_result_when_it_has_some(clip, tmp_path):
    found = facts(clip)
    added = lossless_clip(tmp_path / "added.mp4", frames=10, fps=10, audio=True, pattern="mandelbrot")

    out = clips.splice_append(clip, found, 20, added, 10, True, tmp_path / "out.mp4")

    assert facts(out).has_audio
    assert abs(facts(out).duration - 3.0) < 0.2


def test_conform_can_skip_the_leading_frames_of_a_joined_result(clip, tmp_path):
    found = facts(clip)

    tail = clips.conform(clip, found, tmp_path / "tail.mp4", skip=12)

    assert frame_digests(tail) == frame_digests(clip)[12:]
