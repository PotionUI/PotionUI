from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from src.platform.media_tools.effects.parity import (
    ParityCase,
    ParityError,
    fixture_frame,
    golden_name,
    over_tolerance,
    parity_cases,
    parity_metric,
    read_golden,
    render_case,
    score_case,
    write_golden,
)
from src.platform.media_tools.effects.spec import EffectError, parse_effect
from tests.platform.media_tools.effects.builder import BASE, effect_data
from tests.platform.media_tools.effects.support import core_effect


def parity(**changes) -> dict:
    return {**BASE["parity"], **changes}


def test_the_fixture_frame_is_deterministic_and_covers_the_full_range_at_odd_sizes():
    frame = fixture_frame(641, 359)

    assert frame.shape == (359, 641, 3) and frame.dtype == np.uint8
    assert np.array_equal(frame, fixture_frame(641, 359))
    assert frame.min() == 0 and frame.max() == 255
    assert len(np.unique(frame.reshape(-1, 3), axis=0)) > 1000


def test_cases_number_their_frames_and_carry_the_shared_analysis():
    cases = parity_cases(core_effect("fix_colors"))

    assert [case.index for case in cases] == [0, 1]
    assert cases[0].analysis == {"gain_r": 1.12, "gain_g": 0.97, "gain_b": 0.91, "black": 0.04, "white": 0.93}
    assert golden_name(cases[1], 7) == "c1_f7.png"


def test_pixel_scores_report_the_worst_mean_and_99th_percentile_difference():
    want = {0: np.zeros((10, 10, 3), dtype=np.uint8), 4: np.zeros((10, 10, 3), dtype=np.uint8)}
    got = {0: want[0].copy(), 4: want[4].copy()}
    got[4][0, 0, 0] = 9

    score = score_case("pixels", want, got, want[0])

    assert score["max_abs"] == 9 and score["mean_abs"] == pytest.approx(9 / 600) and score["p99_abs"] == 0


def test_stats_scores_compare_the_change_each_side_makes_not_the_pixels():
    source = np.full((20, 20, 3), 100, dtype=np.uint8)
    rng = np.random.default_rng(0)
    want = {0: (source + rng.integers(-5, 6, size=source.shape)).astype(np.uint8)}
    shuffled = {0: rng.permutation(want[0].reshape(-1, 3)).reshape(source.shape)}
    brighter = {0: (want[0] + 3).astype(np.uint8)}

    assert score_case("stats", want, shuffled, source) == pytest.approx({"mean_abs": 0, "std_abs": 0}, abs=1e-9)
    assert score_case("stats", want, brighter, source)["mean_abs"] == pytest.approx(3)
    assert score_case("pixels", want, shuffled, source)["max_abs"] > 0


def test_scores_refuse_frames_that_do_not_line_up():
    frame = np.zeros((4, 4, 3), dtype=np.uint8)

    with pytest.raises(ParityError, match="different frames"):
        score_case("pixels", {0: frame}, {1: frame}, frame)
    with pytest.raises(ParityError, match="does not match"):
        score_case("pixels", {0: frame}, {0: np.zeros((4, 5, 3), dtype=np.uint8)}, frame)


def test_only_the_limits_a_score_exceeds_are_reported():
    assert over_tolerance({"max_abs": 2, "mean_abs": 0.01, "p99_abs": 1}, {"max_abs": 1, "mean_abs": 0.05}) == ["max_abs 2 > 1"]
    assert over_tolerance({"max_abs": 1, "mean_abs": 0.05}, {"max_abs": 1, "mean_abs": 0.05}) == []


def test_goldens_round_trip_losslessly(tmp_path):
    frame = fixture_frame(33, 17)

    write_golden(tmp_path / "x" / "c0_f0.png", frame)

    assert np.array_equal(read_golden(tmp_path / "x" / "c0_f0.png"), frame)


def test_an_ffmpeg_candidate_renders_the_requested_frames_in_order(need_ffmpeg):
    effect = core_effect("fade")
    case = ParityCase(index=0, params={"fade_in": 1, "fade_out": 1}, frames=(0, 50, 99))

    out = render_case(effect, effect.render[0], case)

    source = fixture_frame(320, 180)
    assert list(out) == [0, 50, 99]
    assert out[0].max() == 0 and out[99].max() == 0
    assert np.array_equal(out[50], source)


def test_a_python_candidate_sees_the_case_frame_indices():
    effect = core_effect("film_grain")
    case = ParityCase(index=0, params={"amount": 100, "seed": 3}, frames=(0, 1))

    out = render_case(effect, effect.render[-1], case)

    assert not np.array_equal(out[0], out[1])


def test_an_effect_with_an_analysis_needs_parity_analysis_values():
    effect = core_effect("fix_colors")
    case = dataclasses.replace(parity_cases(effect)[0], analysis=None)

    with pytest.raises(ParityError, match="parity.analysis"):
        render_case(effect, effect.render[0], case)


def test_grain_compares_statistics_and_the_rest_compare_pixels():
    assert parity_metric(core_effect("film_grain")) == "stats"
    assert {parity_metric(core_effect(name)) for name in ("fix_colors", "vignette", "fade", "ken_burns")} == {"pixels"}


@pytest.mark.parametrize(
    "block, message",
    [
        (parity(metric="noise"), "parity.metric must be one of"),
        (parity(metric="stats", tolerance={"mean_abs": 0.1}), "needs mean_abs and std_abs"),
        (parity(tolerance={"max_abs": 1, "mean_abs": 0.1, "std_abs": 1}), "tolerance takes"),
        (parity(tolerance={"max_abs": -1, "mean_abs": 0.1}), "at least 0"),
        (parity(tolerance={"max_abs": True, "mean_abs": 0.1}), "at least 0"),
    ],
)
def test_the_parity_block_is_checked_when_the_effect_loads(tmp_path, block, message):
    with pytest.raises(EffectError, match=message):
        parse_effect(effect_data(parity=block), tmp_path, "core")


def test_a_stats_parity_block_with_both_limits_loads(tmp_path):
    effect = parse_effect(effect_data(parity=parity(metric="stats", tolerance={"mean_abs": 0.1, "std_abs": 0.2})), tmp_path, "core")

    assert parity_metric(effect) == "stats"
