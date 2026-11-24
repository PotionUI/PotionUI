from unittest.mock import patch

import numpy as np
from PIL import Image

from src.pipelines.outputs import CompareImagesGenerationOutput
from src.pipelines.pipes.detailer.native.detection import CombinedDetector, FaceDetection
from src.pipelines.pipes.detailer.native.main import DetailerNativePipe
from tests.pipelines.pipes.detailer.native.test_pipe import _FakeDetector, _base_config, _run


class _FakeHandDetector:
    def __init__(self, boxes):
        self.boxes = list(boxes)

    def detect(self, image):
        return [FaceDetection(box=tuple(float(v) for v in box), kind="hand") for box in self.boxes]


def _run_with_hands(config, image, face_boxes, hand_boxes):
    face_detector = _FakeDetector(face_boxes)
    hand_detector = _FakeHandDetector(hand_boxes)
    with patch("src.pipelines.pipes.detailer.native.main.build_hand_detector",
               return_value=hand_detector) as hand_build:
        result, outputs, denoise, _ = _run(config, image, face_detector)
    return result, outputs, denoise, hand_build


def _warnings(outputs):
    return [o for o in outputs if "hand" in str(getattr(o, "state", "")).lower()
            and "skipped" in str(getattr(o, "state", "")).lower()]


def _hand_model(tmp_path):
    path = tmp_path / "hand.task"
    path.write_bytes(b"x")
    return path.as_posix()


def test_hand_detection_is_off_by_default():
    assert DetailerNativePipe.get_default_config()["detect_hands"] is False


def test_hands_off_runs_faces_only_and_never_builds_a_hand_detector(tmp_path):
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    config = _base_config(detect_hands=False, hand_model=_hand_model(tmp_path))
    _, _, denoise, hand_build = _run_with_hands(
        config, img, [(100, 100, 180, 180)], [(300, 300, 360, 360)],
    )

    assert denoise.call_count == 1
    hand_build.assert_not_called()


def test_hands_on_refines_faces_and_hands(tmp_path):
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    config = _base_config(detect_hands=True, hand_model=_hand_model(tmp_path), colour_match=False)
    result, outputs, denoise, hand_build = _run_with_hands(
        config, img, [(100, 100, 180, 180)], [(300, 300, 360, 360)],
    )

    assert denoise.call_count == 2
    hand_build.assert_called_once()
    out = np.asarray(result.output["image"][0])
    assert all(abs(int(v) - 200) <= 2 for v in out[330, 330])
    assert tuple(out[480, 480]) == (50, 60, 70)
    assert len([o for o in outputs if isinstance(o, CompareImagesGenerationOutput)]) == 2


def test_small_hands_survive_the_face_size_floor(tmp_path):
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    config = _base_config(detect_hands=True, hand_model=_hand_model(tmp_path))
    _, _, denoise, _ = _run_with_hands(config, img, [], [(300, 300, 340, 340)])

    assert denoise.call_count == 1


def test_max_hands_caps_hands_independently_of_faces(tmp_path):
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    config = _base_config(detect_hands=True, hand_model=_hand_model(tmp_path), max_hands=1)
    hands = [(300, 300, 360, 360), (380, 380, 440, 440), (20, 400, 80, 460)]
    _, _, denoise, _ = _run_with_hands(config, img, [(100, 100, 180, 180)], hands)

    assert denoise.call_count == 2


def test_missing_hand_model_keeps_faces_and_warns(tmp_path):
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    config = _base_config(detect_hands=True, hand_model=(tmp_path / "absent.task").as_posix())
    _, outputs, denoise, hand_build = _run_with_hands(
        config, img, [(100, 100, 180, 180)], [(300, 300, 360, 360)],
    )

    assert denoise.call_count == 1
    hand_build.assert_not_called()
    assert _warnings(outputs)


def test_unset_hand_model_keeps_faces_and_warns():
    img = Image.new("RGB", (512, 512), (50, 60, 70))
    config = _base_config(detect_hands=True, hand_model=None)
    _, outputs, denoise, hand_build = _run_with_hands(
        config, img, [(100, 100, 180, 180)], [(300, 300, 360, 360)],
    )

    assert denoise.call_count == 1
    hand_build.assert_not_called()
    assert _warnings(outputs)


def test_combined_detector_concatenates_both_kinds():
    img = Image.new("RGB", (64, 64))
    combined = CombinedDetector([_FakeDetector([(1, 1, 5, 5)]), _FakeHandDetector([(10, 10, 20, 20)])])

    assert [d.kind for d in combined.detect(img)] == ["face", "hand"]
