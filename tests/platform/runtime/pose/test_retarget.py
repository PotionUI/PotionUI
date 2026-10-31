import numpy as np
import pytest

from src.platform.runtime.pose.layout import (
    BODY_PARENT,
    FACE,
    L_ANKLE,
    L_EAR,
    L_ELBOW,
    L_EYE,
    L_HIP,
    L_KNEE,
    L_WRIST,
    LEFT_HAND,
    NECK,
    NOSE,
    R_ANKLE,
    R_EAR,
    R_ELBOW,
    R_EYE,
    R_HIP,
    R_KNEE,
    R_SHOULDER,
    R_WRIST,
    RIGHT_HAND,
)
from src.platform.runtime.pose.retarget import RetargetError, ease_weights, retarget_sequence
from tests.fixtures.pose_skeletons import DEFAULT_LENGTHS, dance, direction, length, skeleton

THRESHOLD = 0.3


def retarget(driver, subject, **kwargs):
    return retarget_sequence(driver, subject, THRESHOLD, **kwargs)


class TestBoneLengthsAndAngles:
    def test_tall_driver_onto_short_subject_uses_subject_lengths_and_driver_angles(self):
        driver = dance(24, size=2.0)
        subject = skeleton(neck=(300.0, 80.0), size=1.0, hands=False, face=False)
        result = retarget(driver, subject).poses
        for t in (0, 6, 12, 18):
            for joint in BODY_PARENT:
                if joint in (L_ANKLE, R_ANKLE):
                    continue
                assert length(result[t], joint) == pytest.approx(length(subject, joint), rel=0.02)
                assert direction(result[t], joint) == pytest.approx(direction(driver[t], joint), abs=1e-3)

    def test_subject_with_different_proportions_keeps_its_own_bones(self):
        driver = dance(24)
        long_arms = {R_ELBOW: 54.0, R_WRIST: 48.0, L_ELBOW: 54.0, L_WRIST: 48.0}
        subject = skeleton(neck=(100.0, 100.0), lengths=long_arms, hands=False, face=False)
        result = retarget(driver, subject).poses
        assert length(result[5], R_ELBOW) == pytest.approx(54.0, rel=0.02)
        assert length(result[5], R_WRIST) == pytest.approx(48.0, rel=0.02)
        assert length(result[5], R_KNEE) == pytest.approx(DEFAULT_LENGTHS[R_KNEE], rel=0.02)

    def test_foreshortening_of_the_driver_is_kept(self):
        driver = dance(24)
        driver[10, R_WRIST, :2] = driver[10, R_ELBOW, :2] + (driver[10, R_WRIST, :2] - driver[10, R_ELBOW, :2]) * 0.5
        subject = skeleton(size=1.5, hands=False, face=False)
        result = retarget(driver, subject).poses
        full = length(result[11], R_WRIST)
        short = length(result[10], R_WRIST)
        assert full == pytest.approx(length(subject, R_WRIST), rel=0.02)
        assert short == pytest.approx(full * 0.5, rel=0.08)

    def test_an_implausible_subject_bone_is_held_near_the_overall_scale(self):
        driver = dance(12)
        subject = skeleton(hands=False, face=False, lengths={R_WRIST: 96.0, L_WRIST: 96.0})
        result = retarget(driver, subject)
        capped = length(result.poses[3], R_WRIST)
        assert capped == pytest.approx(length(driver[3], R_WRIST) * 1.6 * result.scale, rel=0.02)

    def test_a_foreshortened_subject_arm_borrows_the_other_arms_length(self):
        driver = dance(12)
        subject = skeleton(hands=False, face=False)
        pointing = subject.copy()
        pointing[R_ELBOW, :2] = pointing[R_SHOULDER, :2] + (pointing[R_ELBOW, :2] - pointing[R_SHOULDER, :2]) * 0.3
        result = retarget(driver, pointing).poses
        assert length(result[3], R_ELBOW) == pytest.approx(DEFAULT_LENGTHS[R_ELBOW], rel=0.05)

    def test_overall_scale_is_reported(self):
        driver = dance(12, size=2.0)
        subject = skeleton(size=1.0, hands=False, face=False)
        assert retarget(driver, subject).scale == pytest.approx(0.5, rel=0.02)


class TestRoot:
    def test_first_frame_neck_lands_on_the_subjects_neck(self):
        driver = dance(12)
        subject = skeleton(neck=(333.0, 77.0), hands=False, face=False)
        result = retarget(driver, subject).poses
        assert result[0, NECK, :2] == pytest.approx([333.0, 77.0], abs=1e-3)

    def test_driver_root_path_is_scaled_by_subject_size(self):
        driver = dance(24, size=2.0)
        subject = skeleton(neck=(300.0, 80.0), size=1.0, hands=False, face=False)
        result = retarget(driver, subject).poses
        driver_shift = driver[20, NECK, :2] - driver[0, NECK, :2]
        shift = result[20, NECK, :2] - result[0, NECK, :2]
        assert shift == pytest.approx(driver_shift * 0.5, abs=0.2)

    def test_anchor_survives_a_hidden_subject_neck(self):
        driver = dance(12)
        subject = skeleton(neck=(300.0, 80.0), hands=False, face=False, hidden=(NECK,))
        result = retarget(driver, subject).poses
        assert result[0, NECK, 2] > THRESHOLD
        assert np.isfinite(result).all()


class TestPartialPicture:
    def test_hidden_legs_are_still_drawn_from_the_driver_scaled_by_the_visible_part(self):
        driver = dance(12, size=2.0)
        subject = skeleton(size=1.0, hands=False, face=False, hidden=(R_HIP, R_KNEE, R_ANKLE, L_HIP, L_KNEE, L_ANKLE))
        result = retarget(driver, subject)
        assert result.scale == pytest.approx(0.5, rel=0.05)
        assert (result.poses[:, R_KNEE, 2] > THRESHOLD).all()
        assert length(result.poses[4], R_KNEE) == pytest.approx(length(driver[4], R_KNEE) * result.scale, rel=0.05)

    def test_a_picture_with_no_usable_body_raises(self):
        subject = skeleton(hands=False, face=False)
        subject[:, 2] = 0.0
        with pytest.raises(RetargetError):
            retarget(dance(6), subject)


class TestDriverGaps:
    def test_joints_missing_in_the_driver_are_not_invented(self):
        driver = dance(10)
        driver[:, R_ANKLE, 2] = 0.0
        subject = skeleton(hands=False, face=False)
        result = retarget(driver, subject).poses
        assert (result[:, R_ANKLE, 2] == 0.0).all()
        assert (result[:, L_ANKLE, 2] > THRESHOLD).all()

    def test_a_missing_elbow_still_places_the_wrist(self):
        driver = dance(10)
        driver[4, R_ELBOW, 2] = 0.0
        subject = skeleton(hands=False, face=False)
        result = retarget(driver, subject).poses
        assert result[4, R_WRIST, 2] > THRESHOLD
        assert result[4, R_ELBOW, 2] == 0.0

    def test_driver_without_a_neck_raises(self):
        driver = dance(5)
        driver[:, NECK, 2] = 0.0
        with pytest.raises(RetargetError):
            retarget(driver, skeleton())


class TestHeadAndHands:
    def test_face_and_head_points_follow_the_nose_with_the_head_scale(self):
        driver = dance(12, size=2.0)
        subject = skeleton(size=1.0)
        result = retarget(driver, subject).poses
        for t in (0, 7):
            for point in (FACE.start, FACE.start + 20, R_EYE, L_EYE, R_EAR, L_EAR):
                expected = result[t, NOSE, :2] + (driver[t, point, :2] - driver[t, NOSE, :2]) * 0.5
                assert result[t, point, :2] == pytest.approx(expected, abs=0.1)

    def test_hands_follow_the_wrist_with_the_forearm_scale(self):
        driver = dance(12, size=2.0)
        subject = skeleton(size=1.0)
        result = retarget(driver, subject).poses
        for hand, wrist in ((LEFT_HAND, L_WRIST), (RIGHT_HAND, R_WRIST)):
            for k in (0, 7, 20):
                point = hand.start + k
                expected = result[5, wrist, :2] + (driver[5, point, :2] - driver[5, hand.start, :2]) * 0.5
                assert result[5, point, :2] == pytest.approx(expected, abs=0.1)

    def test_hands_of_a_lost_wrist_are_dropped(self):
        driver = dance(8)
        driver[3, R_WRIST, 2] = 0.0
        driver[3, RIGHT_HAND, 2] = 0.9
        result = retarget(driver, skeleton()).poses
        assert (result[3, RIGHT_HAND, 2] == 0.0).all()

    def test_output_keeps_driver_scores(self):
        driver = dance(6)
        driver[:, R_ELBOW, 2] = 0.55
        result = retarget(driver, skeleton()).poses
        assert result[2, R_ELBOW, 2] == pytest.approx(0.55)


class TestEaseIn:
    def test_first_frame_is_the_subjects_own_pose_then_moves_into_the_driver(self):
        driver = dance(24)
        subject = skeleton(neck=(300.0, 80.0), angles={R_ELBOW: 150.0, R_WRIST: 170.0})
        plain = retarget(driver, subject).poses
        eased = retarget(driver, subject, ease_in_frames=8).poses
        assert eased[0, :, :2] == pytest.approx(subject[:, :2], abs=1e-3)
        assert not np.allclose(eased[0], plain[0])
        assert eased[4, R_WRIST, :2] != pytest.approx(plain[4, R_WRIST, :2], abs=0.5)
        assert eased[8:] == pytest.approx(plain[8:])

    def test_ease_weights_are_a_smoothstep_ramp(self):
        weights = ease_weights(10, 4)
        assert weights[0] == 0.0 and weights[4] == 1.0 and weights[-1] == 1.0
        assert 0.0 < weights[2] < 1.0
        assert (np.diff(weights) >= 0).all()
        assert (ease_weights(5, 0) == 1.0).all()
