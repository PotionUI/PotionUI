import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

import src.plugin_api as root
from src.platform.filesystem.model_roots import ModelRoot, TypeDir
from src.platform.util import face_boxes, face_models
from src.plugin_api import media
from tests.fixtures.model_index_fixtures import FakeProbe, make_resolver

CONTENT = b"yunet weights"
NO_POINTS = [[0.0, 0.0]] * 5


@pytest.fixture
def known(monkeypatch):
    monkeypatch.setattr(face_models, "MODEL_FILES", {face_models.FACE_MODEL: (len(CONTENT), hashlib.sha256(CONTENT).hexdigest())})
    monkeypatch.setattr(face_models, "_verified_files", set())


def _roots(*folders, offline=()):
    roots, bindings = [], []
    for index, folder in enumerate(folders):
        folder.mkdir(parents=True, exist_ok=True)
        root_id = f"root-{index}"
        roots.append(ModelRoot(id=root_id, label=root_id, path=folder.parent, kind="library", read_only=False, case_insensitive=False, state="online", state_reason=None, raw_path=str(folder.parent)))
        bindings.append(TypeDir(root_id=root_id, model_type="detection_bbox", path=folder, position=index, is_write=index == 0, subdir="detection_bbox"))
    resolver = make_resolver(roots, bindings)
    resolver._probe = FakeProbe({f"root-{index}": ("offline", "unmounted") for index in offline})
    return SimpleNamespace(model_roots=resolver)


def _refuse_opening(monkeypatch, refused):
    real_open = Path.open

    def opener(self, *args, **kwargs):
        if self == refused:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opener)


def _row(x, y, width, height, score, points=None):
    return [x, y, width, height, *(points or [0.0] * 10), score]


def test_the_face_detector_says_which_file_it_reads_and_whether_it_is_here(tmp_path, known):
    first, second = tmp_path / "a" / "detection_bbox", tmp_path / "b" / "detection_bbox"
    with patch("src.plugin_api.media.get_container", return_value=_roots(first, second)):
        before = media.face_detector()
        (second / face_models.FACE_MODEL).write_bytes(CONTENT)
        after = media.face_detector()

    assert before == {"model": "face_detection_yunet_2023mar.onnx", "available": False}
    assert after == {"model": "face_detection_yunet_2023mar.onnx", "available": True}


def test_a_file_of_that_name_that_is_not_the_detector_is_not_used(tmp_path, known):
    folder = tmp_path / "a" / "detection_bbox"
    world = _roots(folder)
    (folder / face_models.FACE_MODEL).write_bytes(b"other weights")
    with patch("src.plugin_api.media.get_container", return_value=world), patch.object(face_boxes, "detect") as detect:
        available = media.face_detector()["available"]
        with pytest.raises(FileNotFoundError):
            media.detect_faces(["a.png"])
        (folder / face_models.FACE_MODEL).write_bytes(CONTENT[::-1])
        same_size = media.face_detector()["available"]

    assert available is False
    assert same_size is False
    detect.assert_not_called()


def test_detecting_faces_reads_the_first_detection_bbox_folder_that_holds_the_detector(tmp_path, known):
    first, second, third = (tmp_path / name / "detection_bbox" for name in "abc")
    world = _roots(first, second, third)
    (first / face_models.FACE_MODEL).write_bytes(b"other weights")
    (second / face_models.FACE_MODEL).write_bytes(CONTENT)
    (third / face_models.FACE_MODEL).write_bytes(CONTENT)
    with patch("src.plugin_api.media.get_container", return_value=world), patch.object(face_boxes, "detect", return_value=[[]]) as detect:
        found = media.detect_faces(("a.png",), min_score=0.6)

    assert found == [[]]
    detect.assert_called_once_with(second / face_models.FACE_MODEL, ["a.png"], 0.6)


@pytest.mark.parametrize("min_score", [True, False, -0.01, 1.01, float("nan"), float("inf"), "0.5", None])
def test_a_min_score_that_is_not_a_number_from_zero_to_one_is_refused_before_anything_is_read(tmp_path, known, min_score):
    folder = tmp_path / "a" / "detection_bbox"
    world = _roots(folder)
    (folder / face_models.FACE_MODEL).write_bytes(CONTENT)
    with patch("src.plugin_api.media.get_container", return_value=world), patch.object(face_boxes, "detect") as detect:
        with pytest.raises(ValueError, match="min_score"):
            media.detect_faces(["a.png"], min_score=min_score)

    detect.assert_not_called()


@pytest.mark.parametrize("min_score", [0, 0.0, 1, 1.0])
def test_the_bounds_zero_and_one_are_valid_min_scores(tmp_path, known, min_score):
    folder = tmp_path / "a" / "detection_bbox"
    world = _roots(folder)
    (folder / face_models.FACE_MODEL).write_bytes(CONTENT)
    with patch("src.plugin_api.media.get_container", return_value=world), patch.object(face_boxes, "detect", return_value=[[]]) as detect:
        media.detect_faces(["a.png"], min_score=min_score)

    detect.assert_called_once_with(folder / face_models.FACE_MODEL, ["a.png"], min_score)


def test_the_folder_of_an_offline_root_is_not_read(tmp_path, known):
    first, second = tmp_path / "a" / "detection_bbox", tmp_path / "b" / "detection_bbox"
    world = _roots(first, second, offline=[0])
    (first / face_models.FACE_MODEL).write_bytes(CONTENT)
    with patch("src.plugin_api.media.get_container", return_value=world), patch.object(face_boxes, "detect", return_value=[[]]) as detect:
        offline = media.face_detector()["available"]
        (second / face_models.FACE_MODEL).write_bytes(CONTENT)
        media.detect_faces(["a.png"])

    assert offline is False
    detect.assert_called_once_with(second / face_models.FACE_MODEL, ["a.png"], 0.5)


def test_without_its_model_file_detecting_faces_says_so_downloads_nothing_and_names_no_path(tmp_path, known):
    folder = tmp_path / "a" / "detection_bbox"
    with patch("src.plugin_api.media.get_container", return_value=_roots(folder)), patch.object(face_boxes, "detect") as detect:
        with pytest.raises(FileNotFoundError) as raised:
            media.detect_faces(["a.png"])

    detect.assert_not_called()
    assert face_models.FACE_MODEL in str(raised.value)
    assert str(tmp_path) not in str(raised.value)
    assert list(folder.iterdir()) == []


def test_a_detector_file_that_cannot_be_read_counts_as_missing_and_its_path_is_not_told(tmp_path, known, monkeypatch):
    folder = tmp_path / "a" / "detection_bbox"
    world = _roots(folder)
    model = folder / face_models.FACE_MODEL
    model.write_bytes(CONTENT)
    _refuse_opening(monkeypatch, model)
    with patch("src.plugin_api.media.get_container", return_value=world), patch.object(face_boxes, "detect") as detect:
        available = media.face_detector()["available"]
        with pytest.raises(FileNotFoundError) as raised:
            media.detect_faces(["a.png"])

    assert available is False
    detect.assert_not_called()
    assert face_models.FACE_MODEL in str(raised.value)
    assert str(tmp_path) not in str(raised.value)
    assert face_models._verified_files == set()


def test_the_model_file_is_read_through_once_until_it_changes(tmp_path, known, monkeypatch):
    folder = tmp_path / "a" / "detection_bbox"
    world = _roots(folder)
    model = folder / face_models.FACE_MODEL
    model.write_bytes(CONTENT)
    hashed = []
    real = face_models._sha256
    monkeypatch.setattr(face_models, "_sha256", lambda path: hashed.append(path) or real(path))
    with patch("src.plugin_api.media.get_container", return_value=world):
        answers = [media.face_detector()["available"] for _ in range(3)]
        model.write_bytes(b"other, longer weights")
        changed = media.face_detector()["available"]

    assert answers == [True, True, True]
    assert changed is False
    assert hashed == [model]


def test_the_rows_yunet_answers_become_boxes_best_first_without_the_weak_and_empty_ones():
    rows = [_row(10, 20, 30, 40, 0.7), _row(100, 20, 50, 50, 0.95), _row(5, 5, 8, 8, 0.3), _row(1, 1, 0, 9, 0.9), _row(1, 1, 9, 0, 0.9)]

    found = face_boxes.boxes(rows, 0.5)

    assert found == [
        {"x": 100.0, "y": 20.0, "width": 50.0, "height": 50.0, "score": 0.95, "landmarks": NO_POINTS},
        {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0, "score": 0.7, "landmarks": NO_POINTS},
    ]
    assert face_boxes.boxes(None, 0.5) == []


def test_a_box_keeps_the_five_landmarks_yunet_answers_in_their_order():
    points = [11.0, 12.0, 21.0, 12.5, 16.0, 18.0, 12.0, 24.0, 20.0, 24.5]

    (found,) = face_boxes.boxes([_row(5, 5, 20, 24, 0.9, points)], 0.5)

    assert found["landmarks"] == [[11.0, 12.0], [21.0, 12.5], [16.0, 18.0], [12.0, 24.0], [20.0, 24.5]]


class Detector:
    def __init__(self):
        self.sizes = []
        self.pictures = []

    def setInputSize(self, size):
        self.sizes.append(size)

    def detect(self, picture):
        self.pictures.append(picture)
        return 1, [_row(4, 6, 20, 20, 0.88)] if picture.shape[1] > 100 else None


def test_each_image_a_path_or_a_picture_is_read_at_its_own_size_by_one_detector(tmp_path):
    cv2 = pytest.importorskip("cv2")
    detector = Detector()
    path = tmp_path / "small.png"
    Image.new("L", (64, 48), 200).save(path)

    with patch.object(cv2.FaceDetectorYN, "create", return_value=detector) as create:
        found = face_boxes.detect(Path("m.onnx"), [Image.new("RGB", (832, 480), (255, 0, 0)), str(path)], 0.5)

    assert found == [[{"x": 4.0, "y": 6.0, "width": 20.0, "height": 20.0, "score": 0.88, "landmarks": NO_POINTS}], []]
    assert detector.sizes == [(832, 480), (64, 48)]
    assert [picture.shape for picture in detector.pictures] == [(480, 832, 3), (48, 64, 3)]
    assert detector.pictures[0][0, 0].tolist() == [0, 0, 255]
    create.assert_called_once_with("m.onnx", "", (832, 480), 0.5, face_boxes.NMS, face_boxes.TOP_K)


REAL_DETECTOR = Path("models/detection_bbox")


@pytest.mark.requires_models
def test_the_real_detector_finds_the_face_of_a_photograph_with_its_landmarks_and_none_in_a_plain_image():
    data = pytest.importorskip("skimage.data")
    if not (REAL_DETECTOR / face_models.FACE_MODEL).is_file():
        pytest.skip("face_detection_yunet_2023mar.onnx is not on this machine")
    photograph = Image.fromarray(data.astronaut())

    with patch("src.plugin_api.media.get_container", return_value=_roots(REAL_DETECTOR.resolve())):
        assert media.face_detector()["available"] is True
        faces, plain = media.detect_faces([photograph, Image.new("RGB", (832, 480), (120, 130, 140))])

    assert plain == []
    assert len(faces) == 1
    (face,) = faces
    assert face["score"] >= 0.9
    for x, y in face["landmarks"]:
        assert face["x"] <= x <= face["x"] + face["width"]
        assert face["y"] <= y <= face["y"] + face["height"]
    right_eye, left_eye, nose, right_mouth, left_mouth = face["landmarks"]
    assert right_eye[0] < left_eye[0]
    assert max(right_eye[1], left_eye[1]) < nose[1] < min(right_mouth[1], left_mouth[1])


def test_the_names_are_listed_by_the_media_module_and_the_root():
    assert {"detect_faces", "face_detector"} <= set(media.__all__)
    assert root.detect_faces is media.detect_faces
    assert root.face_detector is media.face_detector
