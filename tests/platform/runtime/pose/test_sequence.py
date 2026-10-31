import numpy as np
import pytest

from src.platform.runtime.pose.layout import NECK, NUM_POINTS, R_WRIST
from src.platform.runtime.pose.retarget import RetargetError
from src.platform.runtime.pose.sequence import (
    NoPersonError,
    clean_sequence,
    estimate_people,
    estimate_sequence,
    estimate_subject,
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


class TestEstimateSubject:
    def test_largest_detection_is_used_and_coordinates_come_back_in_picture_pixels(self):
        backend = Backend(
            detections=[[0, 0, 20, 40, 0.99], [100, 50, 400, 480, 0.8]],
            pose=skeleton(neck=(256.0, 100.0)),
        )
        picture = np.zeros((1024, 768, 3), dtype=np.uint8)
        pose = estimate_subject(picture, backend)
        assert backend.estimate_calls[0][1].tolist() == [100, 50, 400, 480]
        assert backend.estimate_calls[0][0][1] == 512
        assert pose[NECK, 0] == pytest.approx(256.0 * 768 / backend.estimate_calls[0][0][1], rel=1e-4)

    def test_without_a_detection_the_whole_picture_is_the_box(self):
        backend = Backend(detections=[])
        estimate_subject(np.zeros((600, 400, 3), dtype=np.uint8), backend)
        shape, box = backend.estimate_calls[0]
        assert box.tolist() == [0.0, 0.0, float(shape[1]), float(shape[0])]

    def test_a_bad_box_is_retried_on_the_whole_picture(self):
        class Fussy(Backend):
            def estimate(self, rgb, box):
                self.estimate_calls.append((rgb.shape, np.asarray(box)))
                full = box[2] - box[0] >= rgb.shape[1]
                return self.pose.copy() if full else np.zeros((NUM_POINTS, 3), dtype=np.float32)

        backend = Fussy(detections=[[0, 0, 30, 30, 0.9]])
        pose = estimate_subject(np.zeros((400, 400, 3), dtype=np.uint8), backend)
        assert len(backend.estimate_calls) == 2
        assert pose[NECK, 2] > 0.3

    def test_no_person_raises(self):
        backend = Backend(detections=[], pose=np.zeros((NUM_POINTS, 3), dtype=np.float32))
        with pytest.raises(RetargetError, match="picture"):
            estimate_subject(np.zeros((300, 300, 3), dtype=np.uint8), backend)


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


class TestEstimatePeople:
    class Crowd(Backend):
        def __init__(self, boxes, missing=()):
            super().__init__(detections=boxes)
            self.missing = set(missing)

        def estimate(self, rgb, box):
            self.estimate_calls.append((rgb.shape, np.asarray(box)))
            if len(self.estimate_calls) - 1 in self.missing:
                return np.zeros((NUM_POINTS, 3), dtype=np.float32)
            return skeleton(neck=(float(box[0] + box[2]) / 2, float(box[1]) + 30))

    def test_every_detected_person_gets_a_skeleton_largest_first(self):
        backend = self.Crowd([[0, 0, 40, 90, 0.9], [100, 0, 220, 300, 0.8], [300, 0, 340, 100, 0.7]])
        people = estimate_people(np.zeros((300, 400, 3), dtype=np.uint8), backend, detect_resolution=300)
        assert len(people) == 3
        assert people[0][NECK, 0] == pytest.approx(160.0)

    def test_people_the_model_cannot_read_are_skipped(self):
        backend = self.Crowd([[0, 0, 40, 90, 0.9], [100, 0, 220, 300, 0.8]], missing={0})
        assert len(estimate_people(np.zeros((300, 400, 3), dtype=np.uint8), backend, detect_resolution=300)) == 1

    def test_max_people_caps_the_work(self):
        backend = self.Crowd([[i * 20, 0, i * 20 + 15, 90, 0.9] for i in range(10)])
        assert len(estimate_people(np.zeros((300, 400, 3), dtype=np.uint8), backend, detect_resolution=300, max_people=4)) == 4

    def test_nobody_detected_tries_the_whole_picture(self):
        backend = self.Crowd([])
        people = estimate_people(np.zeros((300, 400, 3), dtype=np.uint8), backend, detect_resolution=300)
        assert len(people) == 1
        assert backend.estimate_calls[0][1].tolist() == [0.0, 0.0, 400.0, 300.0]

    def test_coordinates_are_in_source_pixels(self):
        backend = self.Crowd([[100, 0, 220, 300, 0.8]])
        (person,) = estimate_people(np.zeros((600, 800, 3), dtype=np.uint8), backend, detect_resolution=300)
        assert person[NECK, 0] == pytest.approx(160.0 * 2)
