import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src.bootstrap.errors import register_error_handlers
from src.features.editor_tasks.detection import (
    Detection,
    DetectorRegistrationError,
    DetectorRegistry,
    DetectorUnavailable,
    EditorDetectionService,
    merge_duplicates,
    normalise,
)
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.editor_tasks.routes import build_runs_router
from src.features.editor_tasks.yolo_detector import YoloDetector
from src.platform.http.errors import NotFoundError
from src.platform.security import current_user
from src.platform.security.api_scopes import ApiScope as S
from src.platform.security.user import AccountType, User
from tests.platform.security.test_current_user_keys import FakeResolver

SESSION = "a.session.jwt"
OTHER_SESSION = "b.session.jwt"
READ_KEY = "pui_" + "r" * 40 + "ZZZZZZ"
CONTENT_KEY = "pui_" + "c" * 40 + "ZZZZZZ"
GENERATE_KEY = "pui_" + "g" * 40 + "ZZZZZZ"
URL = "/api/editor/tasks/fix_faces/detections"


def account(user_id):
    return User(id=user_id, username=user_id, email=f"{user_id}@example.com", password_hash="h", account_type=AccountType.USER)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


class Media:
    def __init__(self, tmp_path):
        self.path = tmp_path / "picture.png"
        Image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8)).save(self.path)
        self.seen = []

    @contextmanager
    def render_picture(self, user, ref, steps):
        self.seen.append((user.id, ref, list(steps)))
        if user.id != "u1":
            raise NotFoundError("media_not_found", "Not found")
        yield self.path


def two_faces_and_a_hand(picture):
    return [
        Detection("face", (100.0, 10.0, 140.0, 50.0), 0.9),
        Detection("hand", (10.0, 60.0, 30.0, 90.0), 0.7),
        Detection("face", (20.0, 10.0, 60.0, 50.0), 0.8),
    ]


@pytest.fixture
def api(tmp_path):
    registry = DetectorRegistry()
    registry.register("fake", ("face", "hand"), two_faces_and_a_hand)
    media = Media(tmp_path)
    service = EditorDetectionService(registry, register_core_tasks(TaskRegistry()), media)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(build_runs_router(SimpleNamespace(editor_runs=Mock(), editor_detection=service)))
    resolver = FakeResolver()
    resolver.add(READ_KEY, account("u1"), [S.READ])
    resolver.add(CONTENT_KEY, account("u1"), [S.CONTENT])
    resolver.add(GENERATE_KEY, account("u1"), [S.GENERATE])
    auth = Mock()
    auth.get_user_from_token.side_effect = lambda token: {SESSION: account("u1"), OTHER_SESSION: account("u2")}.get(token)
    previous = (current_user._auth, current_user._key_resolver)
    current_user.set_auth(auth)
    current_user.set_key_resolver(resolver)
    current_user._key_failures.reset("testclient")
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, registry=registry, media=media)
    current_user._auth, current_user._key_resolver = previous


def post(api, token=SESSION, **extra):
    body = {"input": {"item_id": "picture"}, "steps": []}
    body.update(extra)
    return api.client.post(URL, headers=bearer(token), json=body)


def test_the_route_answers_boxes_in_zero_to_one_of_the_picture(api):
    response = post(api)

    data = response.json()["data"]
    assert response.status_code == 200
    assert data["picture"] == {"width": 200, "height": 100}
    assert [(item["id"], item["kind"]) for item in data["detections"]] == [("d1", "face"), ("d2", "face"), ("d3", "hand")]
    first = data["detections"][0]
    assert set(first) == {"id", "kind", "box", "score"}
    assert set(first["box"]) == {"x", "y", "width", "height"}
    assert first["score"] == 0.8


def test_boxes_are_padded_by_a_tenth_of_their_size_and_sorted_top_to_bottom_then_left_to_right(api):
    detections = post(api).json()["data"]["detections"]

    assert detections[0]["box"] == {"x": 0.08, "y": 0.06, "width": 0.24, "height": 0.48}
    assert detections[1]["box"] == {"x": 0.48, "y": 0.06, "width": 0.24, "height": 0.48}
    assert detections[2]["box"] == {"x": 0.04, "y": 0.57, "width": 0.12, "height": 0.36}


def test_the_same_picture_gets_the_same_ids(api):
    assert post(api).json() == post(api).json()


def test_the_picture_is_rendered_from_the_input_and_steps_of_the_caller(api):
    post(api, input={"item_id": "up_1"}, steps=[{"tool": "crop", "params": {}, "enabled": True}])

    user, ref, steps = api.media.seen[-1]
    assert user == "u1" and ref.item_id == "up_1" and len(steps) == 1


def test_a_picture_that_is_not_the_callers_answers_404(api):
    assert post(api, token=OTHER_SESSION).status_code == 404


def test_without_a_detector_the_route_answers_503_detector_unavailable(api):
    api.registry.unregister("fake")

    response = post(api)

    assert response.status_code == 503
    assert "detector_unavailable" in response.text


def test_a_detector_whose_weights_are_missing_answers_503_with_its_reason(api):
    def missing(picture):
        raise DetectorUnavailable("no weights here")

    api.registry.unregister("fake")
    api.registry.register("missing", ("face",), missing)

    response = post(api)

    assert response.status_code == 503 and "no weights here" in response.text


def test_a_missing_detector_does_not_hide_the_one_that_works(api):
    def missing(picture):
        raise DetectorUnavailable("no weights here")

    api.registry.register("missing", ("face",), missing)

    assert len(post(api).json()["data"]["detections"]) == 3


def test_a_body_without_an_input_is_refused(api):
    assert api.client.post(URL, headers=bearer(SESSION), json={"steps": []}).status_code == 422


def test_key_scopes_follow_the_generate_routes(api):
    assert post(api, token=READ_KEY).status_code == 403
    assert post(api, token=CONTENT_KEY).status_code == 403
    assert post(api, token=GENERATE_KEY).status_code == 200


def test_without_a_session_the_route_is_refused(api):
    assert api.client.post(URL, json={"input": {"item_id": "picture"}}).status_code in (401, 403)


def test_a_task_the_registry_does_not_know_answers_404(tmp_path):
    service = EditorDetectionService(DetectorRegistry(), TaskRegistry(), Media(tmp_path))

    with pytest.raises(NotFoundError):
        import asyncio

        asyncio.run(service.detect(SimpleNamespace(id="u1"), "fix_faces", SimpleNamespace(input=None, steps=[])))


def test_a_detector_must_name_known_kinds_and_an_id():
    registry = DetectorRegistry()

    for kinds in ((), ("nose",)):
        with pytest.raises(DetectorRegistrationError):
            registry.register("x", kinds, lambda picture: [])
    with pytest.raises(DetectorRegistrationError):
        registry.register("", ("face",), lambda picture: [])


def test_a_detector_id_belongs_to_the_source_that_registered_it():
    registry = DetectorRegistry()
    registry.register("a", ("face",), lambda picture: [], source="plug")

    with pytest.raises(DetectorRegistrationError):
        registry.register("a", ("face",), lambda picture: [], source="other")
    registry.register("a", ("hand",), lambda picture: [], source="plug")
    assert [item.kinds for item in registry.list()] == [frozenset({"hand"})]


def test_unregistering_a_source_removes_only_its_detectors_and_never_the_core_ones():
    registry = DetectorRegistry()
    registry.register("core", ("face",), lambda picture: [])
    registry.register("a", ("face",), lambda picture: [], source="plug")
    registry.register("b", ("hand",), lambda picture: [], source="plug")

    assert registry.unregister_source("core") == 0
    assert registry.unregister_source("plug") == 2
    assert [item.id for item in registry.list()] == ["core"]
    assert registry.unregister("core") is True and registry.unregister("core") is False


def test_a_detector_cannot_report_a_kind_it_did_not_declare():
    registry = DetectorRegistry()
    registry.register("faces", ("face",), lambda picture: [Detection("hand", (0, 0, 5, 5), 0.9), Detection("face", (0, 0, 5, 5), 0.9)])

    assert [item.kind for item in registry.detect(Image.new("RGB", (10, 10)))] == ["face"]


def test_two_detectors_finding_the_same_face_give_one_box_with_the_better_score():
    items = [
        Detection("face", (10, 10, 50, 50), 0.6),
        Detection("face", (11, 11, 51, 51), 0.9),
        Detection("hand", (10, 10, 50, 50), 0.5),
        Detection("face", (100, 100, 140, 140), 0.4),
    ]

    kept = merge_duplicates(items)

    assert sorted((item.kind, item.score) for item in kept) == [("face", 0.4), ("face", 0.9), ("hand", 0.5)]


def test_padding_stops_at_the_edge_of_the_picture():
    result = normalise([Detection("face", (0.0, 0.0, 100.0, 50.0), 0.5)], (100, 50))

    assert result[0]["box"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


def test_a_flat_box_is_dropped():
    assert normalise([Detection("face", (5.0, 5.0, 5.0, 9.0), 0.5)], (100, 50)) == []


class Roots:
    def __init__(self, *folders):
        self.folders = folders

    def type_dirs(self, model_type):
        assert model_type == "detection_bbox"
        return [SimpleNamespace(path=folder) for folder in self.folders]


@pytest.fixture
def fake_helper(monkeypatch):
    module = ModuleType("src.pipelines.pipes._shared.detection.detailer_helper")
    calls = []

    class Helper:
        def __init__(self, config):
            self.config = config

        def load_detector(self, path):
            calls.append(("load", path))
            return path

        def detect_objects_scored(self, image, model, kind):
            calls.append(("detect", kind, model, self.config["detections"][kind]["confidence"]))
            return [(np.array([1.0, 2.0, 30.0, 40.0]), 0.88)]

    module.DetailerHelper = Helper
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls


def test_the_yolo_detector_without_weights_is_unavailable(tmp_path):
    detector = YoloDetector(Roots(tmp_path))

    with pytest.raises(DetectorUnavailable):
        detector(Image.new("RGB", (64, 64)))


def test_the_yolo_detector_uses_the_best_weights_it_finds_and_loads_each_once(tmp_path, fake_helper):
    (tmp_path / "face_yolov8n.pt").write_bytes(b"x")
    (tmp_path / "face_yolov8s.pt").write_bytes(b"x")
    (tmp_path / "hand_yolov8n.pt").write_bytes(b"x")
    detector = YoloDetector(Roots(tmp_path))

    first = detector(Image.new("RGB", (64, 64)))
    detector(Image.new("RGB", (64, 64)))

    assert sorted((item.kind, item.box, item.score) for item in first) == [
        ("face", (1.0, 2.0, 30.0, 40.0), 0.88),
        ("hand", (1.0, 2.0, 30.0, 40.0), 0.88),
    ]
    loads = [call[1] for call in fake_helper if call[0] == "load"]
    assert sorted(Path(path).name for path in loads) == ["face_yolov8s.pt", "hand_yolov8n.pt"]


def test_the_yolo_detector_answers_only_the_kinds_it_has_weights_for(tmp_path, fake_helper):
    (tmp_path / "hand_yolov8s.pt").write_bytes(b"x")

    found = YoloDetector(Roots(tmp_path))(Image.new("RGB", (64, 64)))

    assert [item.kind for item in found] == ["hand"]


def test_the_yolo_detector_without_its_runtime_is_unavailable(tmp_path, monkeypatch):
    (tmp_path / "face_yolov8s.pt").write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "src.pipelines.pipes._shared.detection.detailer_helper", None)

    with pytest.raises(DetectorUnavailable):
        YoloDetector(Roots(tmp_path))(Image.new("RGB", (64, 64)))
