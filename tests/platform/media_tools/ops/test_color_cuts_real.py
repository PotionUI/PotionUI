from __future__ import annotations

import numpy as np
import pytest

from src.platform.media_tools import probe, run_chain
from src.platform.media_tools.ops import cut_finder
from tests.platform.media_tools.effects.support import effect_spec
from tests.platform.media_tools.media_factory import run_ffmpeg

pytestmark = pytest.mark.usefixtures("need_ffmpeg")

FPS = 24
CUT_FRAME = 24


def encode(*args):
    return [*args, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an"]


@pytest.fixture
def two_shots(tmp_path):
    path = tmp_path / "two.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", f"testsrc=size=64x48:rate={FPS}:duration=1",
        "-f", "lavfi", "-i", f"mandelbrot=size=64x48:rate={FPS}",
        "-filter_complex", f"[1:v]trim=end_frame={FPS},setpts=PTS-STARTPTS[b];[0:v][b]concat=n=2:v=1:a=0",
        *encode(str(path)),
    )
    return probe(path)


@pytest.fixture
def fade(tmp_path):
    path = tmp_path / "fade.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", f"testsrc=size=64x48:rate={FPS}:duration=2",
        "-vf", "fade=t=out:st=0:d=2", *encode(str(path)),
    )
    return probe(path)


@pytest.fixture
def cast(tmp_path):
    path = tmp_path / "cast.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "color=c=0x808080:s=64x48:r=10:d=1.5",
        "-vf", "noise=alls=40:allf=t,colorchannelmixer=rr=1.3:gg=0.9:bb=0.55",
        *encode(str(path)),
    )
    return probe(path)


def channel_means(path, tmp_path):
    raw = tmp_path / "means.rgb"
    run_ffmpeg("-i", str(path), "-pix_fmt", "rgb24", "-f", "rawvideo", str(raw))
    return np.fromfile(raw, dtype=np.uint8).reshape(-1, 3).mean(axis=0)


def test_a_hard_cut_splits_at_the_right_time_into_two_clips_of_matching_length(two_shots, ctx):
    shots = run_chain([(cut_finder.spec(), {"sensitivity": 50, "min_shot_seconds": 0.5})], [two_shots], ctx)

    assert len(shots) == 2
    assert all(shot.kind == "video" and shot.path.is_file() for shot in shots)
    assert shots[0].duration == pytest.approx(CUT_FRAME / FPS, abs=0.1)
    assert shots[1].duration == pytest.approx(two_shots.duration - CUT_FRAME / FPS, abs=0.1)


def test_a_fade_to_black_finds_no_cut(fade, ctx):
    with pytest.raises(Exception, match="No cuts"):
        run_chain([(cut_finder.spec(), {"sensitivity": 50, "min_shot_seconds": 0.5})], [fade], ctx)


def test_fix_colors_makes_a_cast_clip_more_neutral_and_stays_a_valid_video(cast, ctx, tmp_path):
    before = channel_means(cast.path, tmp_path)

    [fixed] = run_chain([(effect_spec("fix_colors"), {"white_balance": True, "contrast": False, "strength": 100})], [cast], ctx)

    after = channel_means(fixed.path, tmp_path)
    assert fixed.kind == "video" and fixed.width == cast.width and fixed.duration == pytest.approx(cast.duration, abs=0.2)
    assert np.ptp(before) > 40
    assert np.ptp(after) < np.ptp(before) * 0.35
