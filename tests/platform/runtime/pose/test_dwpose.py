import hashlib
import sys
import types

import numpy as np
import pytest

from src.platform.runtime.pose import dwpose
from src.platform.runtime.pose.dwpose import (
    DETECTOR_FILENAME,
    POSE_FILENAME,
    DWPoseEstimator,
    PersonDetector,
    PoseModel,
    nms,
    simcc_decode,
    verify_weights,
)
from src.platform.runtime.pose.layout import (
    FACE,
    L_EYE,
    L_SHOULDER,
    LEFT_HAND,
    NECK,
    NOSE,
    R_ANKLE,
    R_EAR,
    R_SHOULDER,
    RIGHT_HAND,
    coco_wholebody_to_openpose,
)


class Meta:
    def __init__(self, name="input", shape=None):
        self.name = name
        self.shape = shape


class FakeDetectorSession:
    def __init__(self, persons):
        self.persons = persons
        self.calls = []

    def get_inputs(self):
        return [Meta("images", [1, 3, 640, 640])]

    def run(self, _outputs, feed):
        tensor = feed["images"]
        self.calls.append(tensor.shape)
        raw = np.zeros((1, 8400, 85), dtype=np.float32)
        raw[..., 2:4] = -10.0
        for cell, (w, h, obj, cls) in enumerate(self.persons):
            raw[0, cell, 2] = np.log(w / 8.0)
            raw[0, cell, 3] = np.log(h / 8.0)
            raw[0, cell, 4] = obj
            raw[0, cell, 5] = cls
        return [raw]


class FakePoseSession:
    def __init__(self, targets, peak=1.0, shape=(1, 3, 384, 288)):
        self.targets = targets
        self.peak = peak
        self.shape = shape
        self.last_input = None

    def get_inputs(self):
        return [Meta("input", list(self.shape))]

    def run(self, _outputs, feed):
        self.last_input = feed["input"]
        simcc_x = np.zeros((1, 133, 576), dtype=np.float32)
        simcc_y = np.zeros((1, 133, 768), dtype=np.float32)
        for k, (u, v, score) in enumerate(self.targets):
            if score <= 0:
                continue
            simcc_x[0, k, int(u * 2)] = score
            simcc_y[0, k, int(v * 2)] = score
        return [simcc_x, simcc_y]


def full_targets(score=0.9):
    return [(10 + k, 20 + k, score) for k in range(133)]


class TestNms:
    def test_overlapping_boxes_keep_the_higher_score(self):
        boxes = np.array([[0, 0, 100, 100], [5, 5, 105, 105], [300, 300, 400, 400]], dtype=np.float32)
        scores = np.array([0.6, 0.9, 0.8], dtype=np.float32)
        assert sorted(nms(boxes, scores, 0.45)) == [1, 2]


class TestDetector:
    def test_person_box_is_decoded_back_to_source_pixels(self):
        session = FakeDetectorSession([(80.0, 160.0, 0.9, 0.9)])
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        boxes = PersonDetector(session)(image)
        assert session.calls == [(1, 3, 640, 640)]
        assert boxes.shape == (1, 5)
        x0, y0, x1, y1, score = boxes[0]
        assert (x1 - x0) == pytest.approx(160.0, abs=0.01)
        assert (y1 - y0) == pytest.approx(320.0, abs=0.01)
        assert score == pytest.approx(0.81, abs=1e-4)

    def test_low_scores_are_dropped(self):
        session = FakeDetectorSession([(80.0, 160.0, 0.5, 0.5)])
        assert PersonDetector(session)(np.zeros((640, 640, 3), dtype=np.uint8)).shape == (0, 5)

    def test_nothing_above_the_pre_threshold_returns_empty(self):
        session = FakeDetectorSession([])
        assert PersonDetector(session)(np.zeros((640, 640, 3), dtype=np.uint8)).shape == (0, 5)


class TestSimccDecode:
    def test_argmax_and_split_ratio(self):
        x = np.zeros((1, 2, 10), dtype=np.float32)
        y = np.zeros((1, 2, 10), dtype=np.float32)
        x[0, 0, 6] = 0.8
        y[0, 0, 4] = 0.5
        locs, scores = simcc_decode(x, y)
        assert locs[0, 0].tolist() == [3.0, 2.0]
        assert scores[0, 0] == pytest.approx(0.5)
        assert scores[0, 1] == 0.0


class TestPoseModel:
    def test_keypoints_are_mapped_back_through_the_crop(self):
        session = FakePoseSession(full_targets())
        model = PoseModel(session)
        points, scores = model(np.zeros((384, 288, 3), dtype=np.uint8), [0, 0, 288, 384])
        assert model.input_size == (288, 384)
        assert session.last_input.shape == (1, 3, 384, 288)
        for k in (0, 17, 132):
            u, v = 10 + k, 20 + k
            assert points[k, 0] == pytest.approx(1.25 * u - 36.0, abs=0.01)
            assert points[k, 1] == pytest.approx(1.25 * v - 48.0, abs=0.01)
        assert scores[5] == pytest.approx(0.9)

    def test_input_is_normalised_with_the_dwpose_statistics(self):
        session = FakePoseSession(full_targets())
        PoseModel(session)(np.full((384, 288, 3), 123, dtype=np.uint8), [0, 0, 288, 384])
        center = session.last_input[0, :, 190, 140]
        assert center[0] == pytest.approx((123 - 123.675) / 58.395, abs=1e-3)
        assert center[2] == pytest.approx((123 - 103.53) / 57.375, abs=1e-3)

    def test_dynamic_input_shape_falls_back_to_the_released_model_size(self):
        session = FakePoseSession(full_targets(), shape=(1, 3, "h", "w"))
        assert PoseModel(session).input_size == (288, 384)


class TestOpenposeLayout:
    def make(self):
        keypoints = np.stack([np.array([k, 1000 + k], dtype=np.float32) for k in range(133)])[None]
        scores = np.full((1, 133), 0.9, dtype=np.float32)
        return keypoints, scores

    def test_shape_and_neck(self):
        keypoints, scores = self.make()
        pose = coco_wholebody_to_openpose(keypoints, scores)[0]
        assert pose.shape == (134, 3)
        assert pose[NECK, :2].tolist() == [5.5, 1005.5]
        assert pose[NECK, 2] == 1.0

    def test_neck_needs_both_shoulders(self):
        keypoints, scores = self.make()
        scores[0, 5] = 0.1
        assert coco_wholebody_to_openpose(keypoints, scores)[0][NECK, 2] == 0.0

    def test_body_reorder_follows_openpose(self):
        keypoints, scores = self.make()
        pose = coco_wholebody_to_openpose(keypoints, scores)[0]
        assert pose[NOSE, 0] == 0
        assert pose[R_SHOULDER, 0] == 6
        assert pose[L_SHOULDER, 0] == 5
        assert pose[R_ANKLE, 0] == 16
        assert pose[L_EYE, 0] == 1
        assert pose[R_EAR, 0] == 4

    def test_face_and_hands_keep_their_blocks(self):
        keypoints, scores = self.make()
        pose = coco_wholebody_to_openpose(keypoints, scores)[0]
        assert pose[FACE.start, 0] == 23
        assert pose[FACE.stop - 1, 0] == 90
        assert pose[LEFT_HAND.start, 0] == 91
        assert pose[RIGHT_HAND.start, 0] == 112
        assert pose[RIGHT_HAND.stop - 1, 0] == 132

    def test_wrong_keypoint_count_is_rejected(self):
        with pytest.raises(ValueError):
            coco_wholebody_to_openpose(np.zeros((1, 17, 2), dtype=np.float32), np.zeros((1, 17), dtype=np.float32))


class TestEstimator:
    def factory(self, paths):
        def build(path):
            paths.append(path)
            if path.endswith(DETECTOR_FILENAME):
                return FakeDetectorSession([(100.0, 200.0, 0.9, 0.9)])
            return FakePoseSession(full_targets())

        return build

    def test_from_files_builds_both_sessions_through_the_factory(self, tmp_path):
        paths = []
        estimator = DWPoseEstimator.from_files(tmp_path, self.factory(paths))
        assert [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in paths] == [DETECTOR_FILENAME, POSE_FILENAME]
        rgb = np.zeros((480, 640, 3), dtype=np.uint8)
        boxes = estimator.detect(rgb)
        assert boxes.shape[0] == 1
        pose = estimator.estimate(rgb, boxes[0, :4])
        assert pose.shape == (134, 3)
        assert pose[:, 2].max() <= 1.0

    def test_frames_are_converted_to_bgr_for_the_models(self, tmp_path):
        seen = {}

        class Spy(FakePoseSession):
            def run(self, outputs, feed):
                seen["input"] = feed["input"]
                return super().run(outputs, feed)

        estimator = DWPoseEstimator.from_files(
            tmp_path, lambda p: FakeDetectorSession([]) if p.endswith(DETECTOR_FILENAME) else Spy(full_targets())
        )
        rgb = np.zeros((384, 288, 3), dtype=np.uint8)
        rgb[..., 0] = 255
        estimator.estimate(rgb, [0, 0, 288, 384])
        centre = seen["input"][0, :, 190, 140]
        assert centre[2] == pytest.approx((255 - 103.53) / 57.375, abs=1e-3)
        assert centre[0] == pytest.approx((0 - 123.675) / 58.395, abs=1e-3)

    def test_missing_weights_without_a_factory_name_the_path(self, tmp_path):
        with pytest.raises(ValueError, match=DETECTOR_FILENAME):
            DWPoseEstimator.from_files(tmp_path)

    def test_unload_drops_the_sessions(self, tmp_path):
        estimator = DWPoseEstimator.from_files(tmp_path, self.factory([]))
        estimator.unload()
        assert estimator.detector is None and estimator.pose is None


class TestVerifyWeights:
    def write(self, directory, size_ok=True):
        for name in (DETECTOR_FILENAME, POSE_FILENAME):
            (directory / name).write_bytes(b"x" * 10)

    def patch(self, monkeypatch):
        digest = hashlib.sha256(b"x" * 10).hexdigest()
        monkeypatch.setattr(dwpose, "WEIGHT_FILES", {name: (10, digest) for name in (DETECTOR_FILENAME, POSE_FILENAME)})
        monkeypatch.setattr(dwpose, "_verified", set())

    def test_matching_files_pass(self, tmp_path, monkeypatch):
        self.write(tmp_path)
        self.patch(monkeypatch)
        verify_weights(tmp_path)

    def test_truncated_file_is_reported(self, tmp_path, monkeypatch):
        self.write(tmp_path)
        self.patch(monkeypatch)
        (tmp_path / POSE_FILENAME).write_bytes(b"x" * 4)
        with pytest.raises(ValueError, match="incomplete or corrupted"):
            verify_weights(tmp_path)

    def test_same_size_wrong_content_is_reported(self, tmp_path, monkeypatch):
        self.write(tmp_path)
        self.patch(monkeypatch)
        (tmp_path / DETECTOR_FILENAME).write_bytes(b"y" * 10)
        with pytest.raises(ValueError, match="incomplete or corrupted"):
            verify_weights(tmp_path)

    def test_missing_file_is_reported(self, tmp_path, monkeypatch):
        self.patch(monkeypatch)
        with pytest.raises(ValueError, match="not found"):
            verify_weights(tmp_path)

    def test_catalog_pins_the_published_sizes_and_hashes(self):
        assert dwpose.WEIGHT_FILES[DETECTOR_FILENAME] == (
            216746733, "7860ae79de6c89a3c1eb72ae9a2756c0ccfbe04b7791bb5880afabd97855a411")
        assert dwpose.WEIGHT_FILES[POSE_FILENAME] == (
            134399116, "724f4ff2439ed61afb86fb8a1951ec39c6220682803b4a8bd4f598cd913b1843")


class TestProviders:
    def install(self, monkeypatch, available):
        created = []

        class Session:
            def __init__(self, path, providers=None):
                created.append(providers)

        module = types.SimpleNamespace(get_available_providers=lambda: available, InferenceSession=Session)
        monkeypatch.setitem(sys.modules, "onnxruntime", module)
        return created

    def test_cpu_only_build_uses_the_cpu_provider(self, monkeypatch):
        created = self.install(monkeypatch, ["CPUExecutionProvider"])
        dwpose.create_onnx_session("/m/yolox_l.onnx")
        assert created == [["CPUExecutionProvider"]]

    def test_cuda_build_puts_cuda_first_then_cpu(self, monkeypatch):
        created = self.install(monkeypatch, ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"])
        dwpose.create_onnx_session("/m/yolox_l.onnx")
        assert created == [["CUDAExecutionProvider", "CPUExecutionProvider"]]

    def test_the_choice_is_logged(self, monkeypatch, caplog):
        self.install(monkeypatch, ["CPUExecutionProvider"])
        with caplog.at_level("INFO", logger=dwpose.logger.name):
            dwpose.create_onnx_session("/m/yolox_l.onnx")
        assert "CPUExecutionProvider" in caplog.text
