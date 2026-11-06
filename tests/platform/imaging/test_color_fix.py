from __future__ import annotations

import numpy as np

from src.platform.imaging.color_fix import (
    ColorFix,
    apply_fix,
    estimate_fix,
    ffmpeg_filters,
    grey_world_gains,
    level_points,
)


def cast_frames(count=4, size=(12, 16), tint=(1.25, 1.0, 0.7)):
    rng = np.random.default_rng(7)
    base = rng.integers(40, 200, size=(count, *size, 1)).astype(np.float64)
    return np.clip(base * np.array(tint), 0, 255).astype(np.uint8)


def channel_spread(frames):
    means = frames.reshape(-1, 3).mean(axis=0)
    return float(means.max() - means.min())


def test_white_balance_moves_a_colour_cast_toward_neutral():
    frames = cast_frames()
    fix = estimate_fix(frames, white_balance=True, contrast=False)

    corrected = apply_fix(frames, fix)

    assert channel_spread(frames) > 40
    assert channel_spread(corrected) < channel_spread(frames) * 0.2


def test_a_cast_pushes_the_dominant_channel_down_and_the_weak_one_up():
    red, green, blue = grey_world_gains(cast_frames())

    assert red < 1.0 < blue
    assert green > red


def test_a_neutral_frame_gets_no_correction():
    grey = np.full((2, 8, 8, 3), 128, dtype=np.uint8)

    fix = estimate_fix(grey, white_balance=True, contrast=False)

    assert fix.is_identity()


def test_all_black_frames_are_left_alone():
    black = np.zeros((2, 8, 8, 3), dtype=np.uint8)

    assert estimate_fix(black, white_balance=True, contrast=True).is_identity()


def test_contrast_stretches_a_flat_low_contrast_clip():
    rng = np.random.default_rng(3)
    flat = rng.integers(90, 170, size=(3, 16, 16, 3)).astype(np.uint8)

    fix = estimate_fix(flat, white_balance=False, contrast=True)
    stretched = apply_fix(flat, fix)

    assert fix.black > 0.2 and fix.white < 0.8
    assert int(stretched.max()) - int(stretched.min()) > int(flat.max()) - int(flat.min()) + 60


def test_level_points_ignore_a_clip_that_already_spans_the_range():
    full = np.linspace(0, 255, 16 * 16 * 3).reshape(1, 16, 16, 3).astype(np.uint8)

    black, white = level_points(full)

    assert black < 0.02 and white > 0.98


def test_strength_zero_is_identity_and_half_is_between():
    fix = ColorFix(gains=(0.8, 1.0, 1.4), black=0.2, white=0.8)

    assert fix.with_strength(0.0).is_identity()
    half = fix.with_strength(0.5)
    assert half.gains == (0.9, 1.0, 1.2)
    assert half.black == 0.1 and half.white == 0.9


def test_ffmpeg_filters_are_empty_for_identity_and_name_the_fix_otherwise():
    assert ffmpeg_filters(ColorFix()) == []

    filters = ffmpeg_filters(ColorFix(gains=(0.8, 1.0, 1.4), black=0.1, white=0.9))

    assert filters[0] == "colorchannelmixer=rr=0.8000:gg=1.0000:bb=1.4000"
    assert filters[1].startswith("colorlevels=rimin=0.1000:gimin=0.1000:bimin=0.1000:rimax=0.9000")


def test_levels_only_emit_no_channel_mixer():
    filters = ffmpeg_filters(ColorFix(black=0.1, white=0.9))

    assert len(filters) == 1 and filters[0].startswith("colorlevels")
