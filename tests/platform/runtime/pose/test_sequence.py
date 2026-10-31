import numpy as np
import pytest

from src.platform.runtime.pose.layout import NECK, NUM_POINTS, R_WRIST
from src.platform.runtime.pose.sequence import (
    NoPersonError,
    clean_sequence,
    estimate_sequence,
    shrink_to_short_side,
)
from src.platform.runtime.pose.temporal import lost_frames
from tests.fixtures.pose_skeletons import dance, skeleton


class Backend:
    def __init__(self, detections=None, pose=None):
        self.detections = detections if detections is not None else [[10, 10, 90, 190, 0.9]]
        self.pose = pose if pose is not None else skeleton(neck=(100.0, 60.0))
        self.detect_shapes = []
        self.estimate_calls = []

    def detect(self, rgb):
        self.detect_shapes.append(rgb.shape)
        return np.array(self.detections, dtype=np.float32).reshape(-1, 5)

    def estimate(self, rgb, box):
        self.estimate_calls.append((rgb.shape, np.asarray(box)))
        return self.pose.copy()


def frames(count, width=1280, height=720):
    return [np.zeros((height, width, 3), dtype=np.uint8) for _ in range(count)]


def test_shrink_sets_the_short_side():
    assert shrink_to_short_side(np.zeros((720, 1280, 3), dtype=np.uint8), 512).shape == (512, 910, 3)
    assert shrink_to_short_side(np.zeros((256, 300, 3), dtype=np.uint8), 512).shape[0] == 512
    same = np.zeros((512, 700, 3), dtype=np.uint8)
    assert shrink_to_short_side(same, 512) is same


class TestEstimateSequence:
    def test_runs_at_the_detect_resolution_and_maps_back_to_the_source(self):
        backend = Backend(pose=skeleton(neck=(455.0, 256.0)))
        poses = estimate_sequence(frames(3), backend, keyframe_interval=8, detect_resolution=512)
        assert backend.detect_shapes[0] == (512, 910, 3)
        assert poses.shape == (3, NUM_POINTS, 3)
        assert poses[0, NECK, 0] == pytest.approx(455.0 * 1280 / 910, rel=1e-4)
        assert poses[0, NECK, 1] == pytest.approx(256.0 * 720 / 512, rel=1e-4)

    def test_progress_callback_sees_every_frame(self):
        seen = []
        estimate_sequence(frames(4), Backend(), on_frame=seen.append)
        assert seen == [0, 1, 2, 3]

    def test_frame_count_is_kept_even_when_nobody_is_found(self):
        backend = Backend(detections=[], pose=np.zeros((NUM_POINTS, 3), dtype=np.float32))
        poses = estimate_sequence(frames(5), backend)
        assert poses.shape[0] == 5
        assert lost_frames(poses, 0.3).all()

    def test_an_empty_video_is_an_error(self):
        with pytest.raises(NoPersonError):
            estimate_sequence([], Backend())


class TestCleanSequence:
    def test_all_frames_lost_raises(self):
        with pytest.raises(NoPersonError, match="video"):
            clean_sequence(np.zeros((6, NUM_POINTS, 3), dtype=np.float32), 24.0)

    def test_short_loss_is_interpolated_and_long_loss_is_held(self):
        raw = dance(40)
        raw[10:12] = 0.0
        raw[20:36] = 0.0
        cleaned = clean_sequence(raw, 24.0, max_gap=4, smoothing=0.0)
        assert not lost_frames(cleaned, 0.3).any()
        assert cleaned[10, NECK, 0] == pytest.approx(raw[9, NECK, 0] + (raw[12, NECK, 0] - raw[9, NECK, 0]) / 3, abs=0.01)
        assert np.array_equal(cleaned[30], cleaned[19])

    def test_wrong_shape_is_rejected(self):
        with pytest.raises(ValueError):
            clean_sequence(np.zeros((4, 10, 3), dtype=np.float32), 24.0)

    def test_smoothing_runs_last(self):
        rng = np.random.default_rng(1)
        raw = np.stack([skeleton() for _ in range(60)])
        raw[..., :2] += rng.normal(0, 1.5, raw[..., :2].shape).astype(np.float32)
        cleaned = clean_sequence(raw, 24.0, smoothing=0.6)
        assert cleaned[:, R_WRIST, :2].std() < raw[:, R_WRIST, :2].std()
