import contextvars
import hashlib
import os
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

import src.plugin_api as root
from src.platform.runtime.model_lifecycle import lifecycle as lifecycle_module
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from src.platform.util import face_identity, face_models
from src.plugin_api import media
from tests.fixtures.model_roots import make_roots

CONTENT = b"auraface weights"
ARCFACE = ((38.2946, 51.6963), (73.5318, 51.5014), (56.0252, 71.7366), (41.5493, 92.3655), (70.7299, 92.2041))


@pytest.fixture
def lifecycle(monkeypatch):
    monkeypatch.setattr(lifecycle_module, "_default_lifecycle", None)
    return ModelLifecycle(gpu_monitor=None, settings=None)


@pytest.fixture
def known(monkeypatch):
    monkeypatch.setattr(face_models, "MODEL_FILES", {face_models.IDENTITY_MODEL: (len(CONTENT), hashlib.sha256(CONTENT).hexdigest())})
    monkeypatch.setattr(face_models, "_verified_files", set())


@pytest.fixture
def onnxruntime(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", ModuleType("onnxruntime"))


def _world(tmp_path, lifecycle):
    return SimpleNamespace(model_roots=make_roots(tmp_path, types=[]), model_lifecycle=lifecycle)


def _model(tmp_path, content=CONTENT):
    path = tmp_path / "models" / "face_recognition" / "auraface" / "glintr100.onnx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _points(scale=1.0, dx=40.0, dy=40.0):
    return [[x * scale + dx, y * scale + dy] for x, y in ARCFACE]


class Session:
    def __init__(self, lifecycle=None):
        self.inputs = []
        self.lifecycle = lifecycle
        self.held = []

    def get_inputs(self):
        return [SimpleNamespace(name="data")]

    def run(self, outputs, feeds):
        import numpy as np

        self.inputs.append(feeds["data"])
        if self.lifecycle is not None:
            self.held.append(self in self.lifecycle.leased_values())
        return [np.array([[3.0, 4.0]], dtype=np.float32)]


class Sessions:
    def __init__(self, lifecycle=None):
        self.built = []
        self.lifecycle = lifecycle

    def __call__(self, path):
        session = Session(self.lifecycle)
        self.built.append((path, session))
        return session


def test_the_face_embedder_is_looked_for_in_the_home_model_root_and_says_whether_it_can_run(tmp_path, lifecycle, known, onnxruntime):
    world = _world(tmp_path, lifecycle)
    with patch("src.plugin_api.media.get_container", return_value=world):
        before = media.face_embedder()
        _model(tmp_path)
        after = media.face_embedder()

    assert before == {"model": "glintr100.onnx", "available": False}
    assert after == {"model": "glintr100.onnx", "available": True}


def test_a_file_of_that_name_that_is_not_auraface_is_not_used(tmp_path, lifecycle, known, onnxruntime, monkeypatch):
    sessions = Sessions()
    monkeypatch.setattr(face_identity, "new_session", sessions)
    _model(tmp_path, content=CONTENT[::-1])
    with patch("src.plugin_api.media.get_container", return_value=_world(tmp_path, lifecycle)):
        available = media.face_embedder()["available"]
        with pytest.raises(FileNotFoundError):
            media.embed_faces([(Image.new("RGB", (200, 200)), _points())])

    assert available is False
    assert sessions.built == []


def test_the_face_embedder_is_unavailable_while_onnxruntime_does_not_import(tmp_path, lifecycle, known, monkeypatch):
    _model(tmp_path)
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    with patch("src.plugin_api.media.get_container", return_value=_world(tmp_path, lifecycle)):
        missing = media.face_embedder()
        monkeypatch.setitem(sys.modules, "onnxruntime", ModuleType("onnxruntime"))
        present = media.face_embedder()

    assert missing == {"model": "glintr100.onnx", "available": False}
    assert present["available"] is True


def test_without_its_model_file_embedding_faces_says_so_loads_nothing_and_names_no_path(tmp_path, lifecycle, known, monkeypatch):
    sessions = Sessions()
    monkeypatch.setattr(face_identity, "new_session", sessions)
    with patch("src.plugin_api.media.get_container", return_value=_world(tmp_path, lifecycle)):
        with pytest.raises(FileNotFoundError) as raised:
            media.embed_faces([("a.png", _points())])

    assert sessions.built == []
    assert not lifecycle.is_cached(face_identity.MODEL_KEY)
    assert "glintr100.onnx" in str(raised.value)
    assert str(tmp_path) not in str(raised.value)


def test_an_identity_file_that_cannot_be_read_counts_as_missing_and_its_path_is_not_told(tmp_path, lifecycle, known, onnxruntime, monkeypatch):
    sessions = Sessions()
    monkeypatch.setattr(face_identity, "new_session", sessions)
    model = _model(tmp_path)
    real_open = Path.open

    def opener(self, *args, **kwargs):
        if self == model:
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opener)
    with patch("src.plugin_api.media.get_container", return_value=_world(tmp_path, lifecycle)):
        available = media.face_embedder()["available"]
        with pytest.raises(FileNotFoundError) as raised:
            media.embed_faces([(Image.new("RGB", (200, 200)), _points())])

    assert available is False
    assert sessions.built == []
    assert not lifecycle.is_cached(face_identity.MODEL_KEY)
    assert "glintr100.onnx" in str(raised.value)
    assert str(tmp_path) not in str(raised.value)


def test_embedding_faces_builds_one_session_through_the_model_lifecycle_and_holds_it_only_during_the_call(tmp_path, lifecycle, known, monkeypatch):
    sessions = Sessions(lifecycle)
    monkeypatch.setattr(face_identity, "new_session", sessions)
    model = _model(tmp_path)
    face = (Image.new("RGB", (200, 200), (255, 255, 255)), _points())
    with patch("src.plugin_api.media.get_container", return_value=_world(tmp_path, lifecycle)):
        first = media.embed_faces([face])
        second = media.embed_faces([face, face])
        cached = lifecycle.is_cached(face_identity.MODEL_KEY)
        held_after = lifecycle.leased_values()
        lifecycle.invalidate(face_identity.MODEL_KEY)
        media.embed_faces([face])

    assert first == [pytest.approx([0.6, 0.8])]
    assert second == [pytest.approx([0.6, 0.8])] * 2
    assert [path for path, _ in sessions.built] == [str(model), str(model)]
    assert cached is True
    assert held_after == []
    assert sessions.built[0][1].held == [True, True, True]


def test_a_model_file_put_in_place_again_gets_a_new_session(tmp_path, lifecycle, known, monkeypatch):
    sessions = Sessions()
    monkeypatch.setattr(face_identity, "new_session", sessions)
    model = _model(tmp_path)
    face = (Image.new("RGB", (200, 200)), _points())
    with patch("src.plugin_api.media.get_container", return_value=_world(tmp_path, lifecycle)):
        media.embed_faces([face])
        stat = model.stat()
        os.utime(model, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10 ** 9))
        media.embed_faces([face])
        media.embed_faces([face])

    assert len(sessions.built) == 2


def test_two_callers_at_once_share_one_session(tmp_path, lifecycle, monkeypatch):
    building = threading.Event()
    release = threading.Event()
    built = []

    def slow(path):
        building.set()
        release.wait(timeout=5)
        built.append(path)
        return Session()

    monkeypatch.setattr(face_identity, "new_session", slow)
    model = _model(tmp_path)
    face = (Image.new("RGB", (200, 200)), _points())
    found = []
    threads = [threading.Thread(target=lambda: found.append(face_identity.embed(lifecycle, model, [face]))) for _ in range(2)]
    threads[0].start()
    assert building.wait(timeout=5)
    threads[1].start()
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert built == [str(model)]
    assert found == [[pytest.approx([0.6, 0.8])]] * 2


def test_embedding_faces_from_inside_a_generation_leaves_that_generations_lease_and_preset_alone(tmp_path, lifecycle, known, monkeypatch):
    monkeypatch.setattr(face_identity, "new_session", Sessions())
    _model(tmp_path)
    world = _world(tmp_path, lifecycle)
    weights = Session()

    def generation():
        lifecycle.begin_generation("preset-a")
        lifecycle.begin_lease("generation-1")
        try:
            with patch("src.plugin_api.media.get_container", return_value=world):
                media.embed_faces([(Image.new("RGB", (200, 200)), _points())])
            lease = lifecycle_module._active_lease_id.get()
            owner = lifecycle_module._cache_owner.get()
            lifecycle.acquire("native/dit/a", "a", lambda: weights)
            held = weights in lifecycle.leased_values()
        finally:
            lifecycle.end_lease("generation-1")
        return lease, owner, held

    assert contextvars.copy_context().run(generation) == ("generation-1", "preset-a", True)
    assert lifecycle.is_cached(face_identity.MODEL_KEY)


def test_faces_whose_landmarks_give_no_alignment_are_refused_before_the_model_loads(tmp_path, lifecycle, monkeypatch):
    pytest.importorskip("cv2")
    sessions = Sessions()
    monkeypatch.setattr(face_identity, "new_session", sessions)
    model = _model(tmp_path)
    image = Image.new("RGB", (200, 200))

    with pytest.raises(ValueError):
        face_identity.embed(lifecycle, model, [(image, _points()), (image, _points()[:4])])
    with pytest.raises(ValueError):
        face_identity.embed(lifecycle, model, [(image, [[50.0, 50.0]] * 5)])

    assert sessions.built == []
    assert lifecycle.stats()["active_leases"] == 0


@pytest.mark.parametrize("face", ["image", "image-points-extra", "image-alone", None])
def test_a_face_that_is_not_an_image_and_its_landmarks_is_refused_with_a_plain_message(tmp_path, lifecycle, known, onnxruntime, monkeypatch, face):
    sessions = Sessions()
    monkeypatch.setattr(face_identity, "new_session", sessions)
    _model(tmp_path)
    image = Image.new("RGB", (200, 200))
    shapes = {"image": [image], "image-points-extra": (image, _points(), "extra"), "image-alone": image, None: None}
    with patch("src.plugin_api.media.get_container", return_value=_world(tmp_path, lifecycle)):
        with pytest.raises(ValueError, match="Each face is a pair: an image and its five landmarks"):
            media.embed_faces([(image, _points()), shapes[face]])

    assert sessions.built == []


@pytest.mark.parametrize("kind", ["str", "path", "bytes", "image"])
def test_a_single_path_or_image_where_a_list_of_faces_is_expected_is_refused_before_the_model_loads(tmp_path, lifecycle, known, onnxruntime, monkeypatch, kind):
    sessions = Sessions()
    monkeypatch.setattr(face_identity, "new_session", sessions)
    _model(tmp_path)
    picture = tmp_path / "single.png"
    Image.new("RGB", (8, 8)).save(picture)
    single = {"str": str(picture), "path": picture, "bytes": str(picture).encode(), "image": Image.new("RGB", (8, 8))}[kind]
    with patch("src.plugin_api.media.get_container", return_value=_world(tmp_path, lifecycle)):
        with pytest.raises(TypeError, match="^faces is a list: wrap a single path or image in a list$"):
            media.embed_faces(single)

    assert sessions.built == []
    assert not lifecycle.is_cached(face_identity.MODEL_KEY)


def test_no_faces_load_nothing(tmp_path, lifecycle, monkeypatch):
    sessions = Sessions()
    monkeypatch.setattr(face_identity, "new_session", sessions)

    assert face_identity.embed(lifecycle, _model(tmp_path), []) == []
    assert sessions.built == []
    assert lifecycle.stats()["entries"] == 0


def test_the_session_runs_on_the_cpu_with_two_threads(monkeypatch):
    made = {}
    module = ModuleType("onnxruntime")
    module.SessionOptions = lambda: SimpleNamespace()
    module.InferenceSession = lambda path, options, providers: made.update(path=path, options=options, providers=providers) or "session"
    monkeypatch.setitem(sys.modules, "onnxruntime", module)

    assert face_identity.new_session("m.onnx") == "session"
    assert made["path"] == "m.onnx"
    assert made["providers"] == ["CPUExecutionProvider"]
    assert (made["options"].intra_op_num_threads, made["options"].inter_op_num_threads) == (2, 1)


def test_a_face_is_aligned_so_that_its_five_landmarks_sit_on_the_arcface_template(tmp_path):
    pytest.importorskip("cv2")
    image = Image.new("RGB", (400, 300), (0, 0, 0))
    points = _points(scale=2.0, dx=60.0, dy=40.0)
    eye_x, eye_y = points[0]
    ImageDraw.Draw(image).rectangle((eye_x - 3, eye_y - 3, eye_x + 3, eye_y + 3), fill=(255, 255, 255))
    path = tmp_path / "face.png"
    image.save(path)

    for source in (image, path, str(path)):
        aligned = face_identity.align(source, points)

        assert aligned.shape == (112, 112, 3)
        assert aligned[52, 38].min() > 200
        assert aligned[52, 74].max() < 50
        assert aligned[72, 56].max() < 50


def test_each_face_is_read_alone_scaled_to_minus_one_one_and_its_vector_normalized(tmp_path, lifecycle, monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    sessions = Sessions()
    monkeypatch.setattr(face_identity, "new_session", sessions)
    white = Image.new("RGB", (200, 200), (255, 255, 255))
    black = Image.new("RGB", (200, 200), (0, 0, 0))

    vectors = face_identity.embed(lifecycle, _model(tmp_path), [(white, _points()), (black, _points())])

    session = sessions.built[0][1]
    assert vectors == [pytest.approx([0.6, 0.8]), pytest.approx([0.6, 0.8])]
    assert [tuple(item.shape) for item in session.inputs] == [(1, 3, 112, 112), (1, 3, 112, 112)]
    assert np.allclose(session.inputs[0][0, :, 56, 56], 1.0)
    assert np.allclose(session.inputs[1][0, :, 56, 56], -1.0)
    assert session.inputs[0].dtype == np.float32


REAL_HOME = Path("models")


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


@pytest.mark.requires_models
def test_the_real_model_gives_one_unit_vector_of_512_values_per_face_close_for_one_person(lifecycle):
    data = pytest.importorskip("skimage.data")
    if not (REAL_HOME / "detection_bbox" / face_models.FACE_MODEL).is_file() or not (REAL_HOME / face_models.IDENTITY_DIR / face_models.IDENTITY_MODEL).is_file():
        pytest.skip("the face models are not on this machine")
    from tests.plugin_api.test_face_detector import _roots

    photograph = Image.fromarray(data.astronaut())
    smaller = photograph.resize((photograph.width // 2, photograph.height // 2), Image.BICUBIC)
    other = Image.fromarray(data.camera()).convert("RGB")
    detector = _roots((REAL_HOME / "detection_bbox").resolve())
    world = SimpleNamespace(model_roots=make_roots(REAL_HOME, types=[], home_dir=REAL_HOME.resolve()), model_lifecycle=lifecycle)

    with patch("src.plugin_api.media.get_container", return_value=detector):
        found = media.detect_faces([photograph, smaller, other])
    with patch("src.plugin_api.media.get_container", return_value=world):
        assert media.face_embedder()["available"] is True
        pictures = [photograph, smaller, other]
        vectors = media.embed_faces([(picture, faces[0]["landmarks"]) for picture, faces in zip(pictures, found) if faces])

    assert [len(faces) >= 1 for faces in found[:2]] == [True, True]
    assert all(len(vector) == 512 and abs(_cosine(vector, vector) - 1.0) < 1e-3 for vector in vectors)
    assert _cosine(vectors[0], vectors[1]) > 0.7
    if len(vectors) == 3:
        assert _cosine(vectors[0], vectors[2]) < 0.3


def test_the_names_are_listed_by_the_media_module_and_the_root():
    assert {"face_embedder", "embed_faces"} <= set(media.__all__)
    assert root.face_embedder is media.face_embedder
    assert root.embed_faces is media.embed_faces
    assert not {"face_identifier", "identify_faces"} & (set(media.__all__) | set(root.__all__))
    assert not hasattr(media, "identify_faces") and not hasattr(media, "face_identifier")
