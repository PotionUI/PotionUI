from fractions import Fraction

import pytest

from src.pipelines.pipes.interpolator.rife.cadence import (
    Slot,
    output_count,
    output_fps,
    slot,
    source_per_output,
)


def _schedule(n_source, src_fps, factor=2, target_fps=0.0):
    ratio = source_per_output(src_fps, factor, target_fps)
    return [slot(k, ratio) for k in range(output_count(n_source, ratio))]


@pytest.mark.parametrize("factor", [2, 4])
@pytest.mark.parametrize("n_source", [1, 2, 7, 121])
def test_factor_mode_gives_the_former_timesteps(factor, n_source):
    schedule = _schedule(n_source, 24.0, factor=factor)

    assert len(schedule) == n_source * factor
    assert [(s.left, s.t) for s in schedule] == [
        (k // factor, (k % factor) / factor) for k in range(n_source * factor)
    ]


def test_factor_mode_holds_the_last_frame_for_its_own_slots():
    schedule = _schedule(5, 24.0, factor=4)

    assert [s.left for s in schedule[-4:]] == [4, 4, 4, 4]
    assert [s.t for s in schedule[-4:]] == [0.0, 0.25, 0.5, 0.75]


def test_16_to_24_keeps_one_frame_in_three():
    schedule = _schedule(81, 16.0, target_fps=24.0)

    assert len(schedule) == 122
    assert [(s.left, round(s.t, 6)) for s in schedule[:6]] == [
        (0, 0.0), (0, 0.666667), (1, 0.333333), (2, 0.0), (2, 0.666667), (3, 0.333333)
    ]
    assert sum(1 for s in schedule if s.t == 0.0) == 41


def test_24_to_60_uses_five_instants():
    schedule = _schedule(120, 24.0, target_fps=60.0)

    assert len(schedule) == 300
    assert sorted({round(s.t, 6) for s in schedule}) == [0.0, 0.2, 0.4, 0.6, 0.8]
    assert sum(1 for s in schedule if s.t == 0.0) == 60


def test_25_to_60_uses_eleven_instants_between_frames():
    schedule = _schedule(125, 25.0, target_fps=60.0)

    assert len(schedule) == 300
    assert len({round(s.t, 6) for s in schedule if s.t > 0}) == 11
    assert sum(1 for s in schedule if s.t == 0.0) == 25


def test_25_to_24_drops_frames_by_the_same_rule():
    schedule = _schedule(125, 25.0, target_fps=24.0)

    assert len(schedule) == 120
    assert schedule[1] == Slot(1, 1, pytest.approx(1 / 24))
    assert schedule[24].left == 25 and schedule[24].t == 0.0


def test_ntsc_rates_are_exact_fractions():
    assert source_per_output(23.976023976023978, target_fps=60.0) == Fraction(24000, 1001) / 60
    assert output_fps(23.976023976023978, target_fps=60.0) == 60.0


def test_output_fps_falls_back_to_the_factor():
    assert output_fps(25.0, factor=2) == 50.0
    assert output_fps(25.0, factor=4, target_fps=0.0) == 100.0


def test_output_count_rounds_and_never_drops_a_clip():
    ratio = source_per_output(24.0, target_fps=60.0)

    assert output_count(0, ratio) == 0
    assert output_count(121, ratio) == round(121 * 60 / 24)
    assert output_count(1, source_per_output(60.0, target_fps=24.0)) == 1


def test_an_instant_within_a_thousandth_snaps_to_the_source_frame():
    ratio = Fraction(10001, 10000)

    assert slot(1, ratio) == Slot(1, 1, 0.0)
    assert slot(20, ratio).t == pytest.approx(0.002)


def test_a_position_a_hair_below_a_frame_lands_on_it():
    ratio = Fraction(999999, 3000000)

    assert slot(3, ratio).left == 1
    assert slot(3, ratio).t == 0.0
