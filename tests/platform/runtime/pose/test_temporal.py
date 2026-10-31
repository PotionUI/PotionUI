import numpy as np
import pytest

from src.platform.runtime.pose.layout import L_WRIST, NECK, R_WRIST
from src.platform.runtime.pose.temporal import fill_gaps, hold_lost, lost_frames, smooth
from tests.fixtures.pose_skeletons import skeleton


def still(frames=30, **kwargs):
    return np.stack([skeleton(**kwargs) for _ in range(frames)])


class TestFillGaps:
    def test_short_gap_is_interpolated_linearly(self):
        poses = still(10)
        poses[:, R_WRIST, 0] = np.arange(10) * 10.0
        poses[3:6, R_WRIST, 2] = 0.0
        filled = fill_gaps(poses, 0.3, 12)
        assert filled[3:6, R_WRIST, 0].tolist() == pytest.approx([30.0, 40.0, 50.0])
        assert (filled[3:6, R_WRIST, 2] >= 0.3).all()

    def test_gap_longer_than_max_stays_dropped(self):
        poses = still(20)
        poses[3:15, R_WRIST, 2] = 0.0
        filled = fill_gaps(poses, 0.3, 5)
        assert (filled[3:15, R_WRIST, 2] == 0.0).all()

    def test_leading_and_trailing_gaps_are_not_invented(self):
        poses = still(10)
        poses[:2, L_WRIST, 2] = 0.0
        poses[-2:, L_WRIST, 2] = 0.0
        filled = fill_gaps(poses, 0.3, 12)
        assert (filled[:2, L_WRIST, 2] == 0.0).all() and (filled[-2:, L_WRIST, 2] == 0.0).all()

    def test_input_is_not_mutated(self):
        poses = still(6)
        poses[2, R_WRIST, 2] = 0.0
        fill_gaps(poses, 0.3, 12)
        assert poses[2, R_WRIST, 2] == 0.0

    def test_lost_person_for_a_few_frames_is_interpolated_whole(self):
        poses = still(10)
        poses[:, :, 0] += np.arange(10)[:, None] * 5.0
        poses[4:6] = 0.0
        filled = fill_gaps(poses, 0.3, 12)
        assert not lost_frames(filled, 0.3).any()
        assert filled[4, NECK, 0] == pytest.approx(poses[3, NECK, 0] + 5.0)


class TestHoldLost:
    def test_long_loss_holds_the_last_good_pose(self):
        poses = still(10)
        poses[:, :, 0] += np.arange(10)[:, None]
        poses[5:] = 0.0
        held = hold_lost(poses, 0.3)
        assert np.array_equal(held[7], poses[4])
        assert not lost_frames(held, 0.3).any()

    def test_leading_loss_takes_the_first_good_pose(self):
        poses = still(6)
        poses[:2] = 0.0
        held = hold_lost(poses, 0.3)
        assert np.array_equal(held[0], poses[2])

    def test_nothing_found_anywhere_raises(self):
        with pytest.raises(ValueError):
            hold_lost(np.zeros((4, 134, 3), dtype=np.float32), 0.3)


class TestSmooth:
    def noisy(self, frames=120, sigma=1.5, seed=3):
        rng = np.random.default_rng(seed)
        poses = still(frames)
        poses[..., :2] += rng.normal(0, sigma, poses[..., :2].shape).astype(np.float32)
        return poses

    def test_static_pose_stays_static_and_jitter_drops(self):
        clean = still(120)
        noisy = self.noisy()
        out = smooth(noisy, 24.0, 0.5, 0.3)
        raw_std = noisy[:, R_WRIST, :2].std(axis=0).mean()
        smooth_std = out[:, R_WRIST, :2].std(axis=0).mean()
        assert smooth_std < raw_std * 0.5
        assert abs(out[:, R_WRIST, :2].mean(axis=0) - clean[0, R_WRIST, :2]).max() < 1.0

    def test_noise_free_static_pose_is_unchanged(self):
        poses = still(40)
        out = smooth(poses, 24.0, 0.7, 0.3)
        assert np.abs(out - poses).max() < 1e-3

    def test_stronger_setting_smooths_more(self):
        noisy = self.noisy()
        light = smooth(noisy, 24.0, 0.2, 0.3)[:, R_WRIST, :2].std(axis=0).mean()
        heavy = smooth(noisy, 24.0, 1.0, 0.3)[:, R_WRIST, :2].std(axis=0).mean()
        assert heavy < light

    def test_a_steady_motion_is_followed_without_lag(self):
        poses = still(60)
        poses[:, :, 0] += np.arange(60)[:, None] * 4.0
        out = smooth(poses, 24.0, 0.5, 0.3)
        assert np.abs(out[10:50, R_WRIST, 0] - poses[10:50, R_WRIST, 0]).max() < 3.0

    def test_strength_zero_is_a_no_op(self):
        noisy = self.noisy(20)
        assert smooth(noisy, 24.0, 0.0, 0.3) is noisy

    def test_invalid_points_keep_their_values_and_scores(self):
        noisy = self.noisy(30)
        noisy[:, L_WRIST, 2] = 0.1
        out = smooth(noisy, 24.0, 0.5, 0.3)
        assert np.array_equal(out[:, L_WRIST], noisy[:, L_WRIST])

    def test_scores_are_untouched(self):
        noisy = self.noisy(30)
        out = smooth(noisy, 24.0, 0.5, 0.3)
        assert np.array_equal(out[..., 2], noisy[..., 2])
