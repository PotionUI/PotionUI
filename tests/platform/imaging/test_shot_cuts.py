from __future__ import annotations

import numpy as np

from src.platform.imaging.shot_cuts import find_cuts, frame_features

FPS = 24.0


def flat(count, color, height=18, width=32):
    return np.broadcast_to(np.array(color, dtype=np.uint8), (count, height, width, 3)).copy()


def textured(count, seed, base, height=18, width=32):
    rng = np.random.default_rng(seed)
    noise = rng.integers(-25, 26, size=(count, height, width, 3))
    return np.clip(np.array(base) + noise, 0, 255).astype(np.uint8)


def cuts_for(frames, sensitivity=50.0, min_shot=0.5):
    return find_cuts(frame_features(frames), FPS, sensitivity, min_shot)


def test_a_hard_cut_is_found_at_the_first_frame_of_the_new_shot():
    frames = np.concatenate([textured(30, 1, (200, 60, 50)), textured(30, 2, (40, 90, 210))])

    assert cuts_for(frames) == [30]


def test_two_cuts_give_two_boundaries_in_order():
    frames = np.concatenate([
        textured(30, 1, (200, 60, 50)),
        textured(30, 2, (40, 90, 210)),
        textured(30, 3, (30, 180, 60)),
    ])

    assert cuts_for(frames) == [30, 60]


def test_a_slow_brightness_ramp_is_not_a_cut():
    ramp = np.concatenate([flat(1, (v, v, v)) for v in np.linspace(20, 230, 72)])

    assert cuts_for(ramp) == []


def test_a_fade_to_black_is_not_a_cut():
    base = textured(1, 5, (180, 150, 120))
    fade = np.concatenate([(base * level).astype(np.uint8) for level in np.linspace(1.0, 0.0, 48)])

    assert cuts_for(fade) == []


def test_a_static_shot_has_no_cuts():
    assert cuts_for(textured(1, 4, (90, 90, 90)).repeat(60, axis=0)) == []


def test_a_cut_right_after_another_is_dropped_by_the_shortest_shot():
    frames = np.concatenate([
        textured(30, 1, (200, 60, 50)),
        textured(3, 2, (40, 90, 210)),
        textured(30, 3, (30, 180, 60)),
    ])

    assert cuts_for(frames, min_shot=0.5) == [30]
    assert cuts_for(frames, min_shot=0.1) == [30, 33]


def test_higher_sensitivity_catches_a_subtle_cut_that_lower_sensitivity_misses():
    frames = np.concatenate([textured(30, 1, (120, 120, 120)), textured(30, 2, (150, 135, 120))])

    assert cuts_for(frames, sensitivity=100.0) == [30]
    assert cuts_for(frames, sensitivity=0.0) == []


def test_a_single_frame_has_nothing_to_compare():
    assert cuts_for(flat(1, (10, 10, 10))) == []


def test_constant_high_motion_is_not_a_run_of_cuts():
    rng = np.random.default_rng(9)
    strobe = np.concatenate([flat(1, tuple(rng.integers(0, 256, size=3))) for _ in range(72)])

    assert cuts_for(strobe) == []


def test_a_real_cut_is_still_found_inside_a_busy_stretch():
    rng = np.random.default_rng(10)
    busy = np.concatenate([flat(1, tuple(rng.integers(0, 256, size=3))) for _ in range(40)])
    frames = np.concatenate([busy, textured(40, 1, (10, 10, 10)), textured(40, 2, (240, 240, 240))])

    assert 80 in cuts_for(frames)
