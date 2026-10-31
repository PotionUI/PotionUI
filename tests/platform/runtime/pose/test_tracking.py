import numpy as np

from src.platform.runtime.pose.layout import BODY, NUM_POINTS, empty_pose
from src.platform.runtime.pose.tracking import PersonTracker, body_count, box_iou, pose_box


class ScriptedBackend:
    def __init__(self, detections, lose_at=()):
        self.detections = detections
        self.lose_at = set(lose_at)
        self.detect_calls = []
        self.estimate_boxes = []
        self.frame = 0

    def detect(self, rgb):
        self.detect_calls.append(self.frame)
        result = self.detections(self.frame)
        return np.array(result, dtype=np.float32).reshape(-1, 5)

    def estimate(self, rgb, box):
        self.estimate_boxes.append(np.asarray(box, dtype=np.float32))
        pose = np.zeros((NUM_POINTS, 3), dtype=np.float32)
        if self.frame in self.lose_at:
            return pose
        x0, y0, x1, y1 = box
        cx = (x0 + x1) / 2 + self.frame
        pose[BODY, 0] = cx
        pose[BODY, 1] = np.linspace(y0 + 10, y1 - 10, 18)
        pose[BODY, 2] = 0.9
        return pose


def run(tracker, backend, frames, size=(200, 200)):
    out = []
    for index in range(frames):
        backend.frame = index
        out.append(tracker.process(np.zeros((size[1], size[0], 3), dtype=np.uint8), index))
    return out


def test_detector_runs_on_keyframes_only():
    backend = ScriptedBackend(lambda f: [[50, 20, 120, 180, 0.9]])
    run(PersonTracker(backend, keyframe_interval=4), backend, 10)
    assert backend.detect_calls == [0, 4, 8]


def test_box_is_tracked_from_the_previous_skeleton_between_keyframes():
    backend = ScriptedBackend(lambda f: [[50, 20, 120, 180, 0.9]])
    run(PersonTracker(backend, keyframe_interval=100), backend, 3)
    first, second, third = backend.estimate_boxes
    assert not np.allclose(first, second)
    assert second[0] > first[0] and third[0] > second[0]


def test_largest_person_is_chosen_first_then_the_best_overlap():
    big = [40, 10, 160, 190, 0.8]
    small = [170, 100, 195, 190, 0.95]
    backend = ScriptedBackend(lambda f: [small, big] if f < 2 else [big, small])
    tracker = PersonTracker(backend, keyframe_interval=1)
    run(tracker, backend, 4)
    assert all(box[0] < 100 for box in backend.estimate_boxes[:1])
    assert all(box[0] < 100 for box in backend.estimate_boxes)


def test_losing_the_person_returns_an_empty_pose_and_redetects_next_frame():
    backend = ScriptedBackend(lambda f: [[50, 20, 120, 180, 0.9]], lose_at={2})
    tracker = PersonTracker(backend, keyframe_interval=100)
    poses = run(tracker, backend, 5)
    assert body_count(poses[2], 0.3) == 0
    assert body_count(poses[3], 0.3) == 18
    assert backend.detect_calls == [0, 3]


def test_no_detection_and_no_track_gives_empty_pose():
    backend = ScriptedBackend(lambda f: [])
    poses = run(PersonTracker(backend, keyframe_interval=1), backend, 3)
    assert all(body_count(p, 0.3) == 0 for p in poses)
    assert backend.estimate_boxes == []


def test_tracking_survives_a_missed_detection_on_a_keyframe():
    backend = ScriptedBackend(lambda f: [[50, 20, 120, 180, 0.9]] if f == 0 else [])
    poses = run(PersonTracker(backend, keyframe_interval=2), backend, 5)
    assert all(body_count(p, 0.3) == 18 for p in poses)


def test_box_iou():
    assert box_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert box_iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert 0.0 < box_iou([0, 0, 10, 10], [5, 5, 15, 15]) < 0.2


def test_pose_box_pads_and_clips_to_the_frame():
    pose = empty_pose()
    pose[BODY, :2] = [[10, 10]] * 9 + [[90, 90]] * 9
    pose[BODY, 2] = 0.9
    box = pose_box(pose, 0.3, 100, 100)
    assert box[0] == 0 and box[1] == 0 and box[2] == 100 and box[3] == 100
    assert pose_box(empty_pose(), 0.3, 100, 100) is None


def test_a_larger_newcomer_does_not_steal_the_track():
    mine = [30, 20, 90, 180, 0.9]
    newcomer = [110, 0, 199, 199, 0.95]
    backend = ScriptedBackend(lambda f: [mine] if f == 0 else [newcomer, mine])
    run(PersonTracker(backend, keyframe_interval=1), backend, 3)
    assert all(box[2] < 110 for box in backend.estimate_boxes)
