import io
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import backend.api as api
from src.plugin_api import AccountType, GenerationNotFoundException, User, get_current_active_user

USER_ID = "user-1"


def _user() -> User:
    return User(
        id=USER_ID,
        username="someuser",
        email="someuser@test.com",
        password_hash="hash",
        account_type=AccountType.USER,
    )


class FakeMediaResult:
    def __init__(self, file_path):
        self.file_path = file_path


class FakeMediaStore:
    def __init__(self, paths):
        self._paths = paths

    def get_generation_media(self, generation_id, filename, user_id=None, size=None, animated=False):
        key = (generation_id, filename)
        if key not in self._paths:
            raise ValueError(f"no media for {key}")
        return FakeMediaResult(self._paths[key])


class FakeQuery:
    def __init__(self, params_by_key, raise_for=None):
        self._params_by_key = params_by_key
        self._raise_for = raise_for or {}

    def get_params(self, generation_id, index, user_id):
        key = (generation_id, index)
        if key in self._raise_for:
            raise self._raise_for[key]
        entry = self._params_by_key.get(key)
        if entry is None:
            return {"generation_id": generation_id, "index": index, "parameters": {}, "models": []}
        return entry


class FakeHistoryFacade:
    def __init__(self, generations, query):
        self._generations = generations
        self.query = query

    def get_by_id(self, generation_id, user_id, include_files=True):
        generation = self._generations.get(generation_id)
        if generation is None:
            raise GenerationNotFoundException(generation_id)
        return generation


class FakeContainer:
    def __init__(self, history, media_store):
        self.generation_history_facade = history
        self.media_store = media_store


def _file(index, file_type, path):
    return {"file_type": file_type, "file_path": path}


@pytest.fixture
def png_dir(tmp_path):
    return tmp_path


def _write_png(directory, name, png_bytes_factory, size=(4, 4)):
    path = directory / name
    path.write_bytes(png_bytes_factory(size=size))
    return str(path)


def _parameters_chunk(png_bytes: bytes):
    with Image.open(io.BytesIO(png_bytes)) as image:
        image.load()
        return image.info.get("parameters")


@pytest.fixture
def client_factory(monkeypatch):
    def build(container):
        monkeypatch.setattr(api, "get_container", lambda: container)

        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[get_current_active_user] = _user
        return TestClient(app)

    return build


def test_export_zip_matches_single_image_route_metadata(png_dir, png_bytes_factory, client_factory):
    path_a = _write_png(png_dir, "a.png", png_bytes_factory)
    path_b = _write_png(png_dir, "b.png", png_bytes_factory)

    generations = {
        "gen-a": {
            "files": [_file(0, "IMAGE", path_a)],
            "form_data": {},
        },
        "gen-b": {
            "files": [_file(0, "IMAGE", path_b)],
            "form_data": {},
        },
    }
    query = FakeQuery({
        ("gen-a", 0): {
            "parameters": {"positive_prompt": "a cat", "steps": 20, "seed": 42},
            "models": [{"model_type": "checkpoint", "name": "SomeCheckpoint", "sha256": "a" * 64}],
        },
        ("gen-b", 0): {
            "parameters": {"positive_prompt": "a dog", "steps": 30, "seed": 7},
            "models": [],
        },
    })
    history = FakeHistoryFacade(generations, query)
    media_store = FakeMediaStore({("gen-a", "a.png"): path_a, ("gen-b", "b.png"): path_b})
    container = FakeContainer(history, media_store)

    client = client_factory(container)

    single_response = client.get("/api/plugins/civitai-provider/export-png", params={"generation_id": "gen-a", "index": 0})
    assert single_response.status_code == 200
    single_parameters = _parameters_chunk(single_response.content)
    assert single_parameters and "a cat" in single_parameters

    zip_response = client.post("/api/plugins/civitai-provider/export-zip", json={"generation_ids": ["gen-a", "gen-b"]})
    assert zip_response.status_code == 200
    assert zip_response.headers["content-type"] == "application/zip"

    with ZipFile(io.BytesIO(zip_response.content)) as archive:
        names = set(archive.namelist())
        assert names == {"gen-a_0.png", "gen-b_0.png", "export-report.txt"}
        assert "export-report.txt" in names

        png_names = [n for n in names if n.endswith(".png")]
        assert len(png_names) == 2

        entry_a = archive.read("gen-a_0.png")
        entry_b = archive.read("gen-b_0.png")

    assert _parameters_chunk(entry_a) == single_parameters
    assert "a dog" in _parameters_chunk(entry_b)


def test_export_zip_skips_unowned_generation(png_dir, png_bytes_factory, client_factory):
    path_a = _write_png(png_dir, "a.png", png_bytes_factory)

    generations = {
        "gen-mine": {"files": [_file(0, "IMAGE", path_a)], "form_data": {}},
    }
    query = FakeQuery({
        ("gen-mine", 0): {"parameters": {"positive_prompt": "mine"}, "models": []},
    })
    history = FakeHistoryFacade(generations, query)
    media_store = FakeMediaStore({("gen-mine", "a.png"): path_a})
    container = FakeContainer(history, media_store)

    client = client_factory(container)

    response = client.post(
        "/api/plugins/civitai-provider/export-zip",
        json={"generation_ids": ["gen-not-mine", "gen-mine"]},
    )
    assert response.status_code == 200

    with ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {"gen-mine_0.png", "export-report.txt"}
        report = archive.read("export-report.txt").decode()
        assert "gen-not-mine" in report
        assert "not found" in report


def test_export_zip_skips_video_and_reports_it(png_dir, png_bytes_factory, client_factory):
    path_image = _write_png(png_dir, "a.png", png_bytes_factory)
    video_path = str(png_dir / "clip.mp4")
    (png_dir / "clip.mp4").write_bytes(b"not a real video, just bytes")

    generations = {
        "gen-mixed": {
            "files": [_file(0, "IMAGE", path_image), _file(1, "VIDEO", video_path)],
            "form_data": {},
        },
    }
    query = FakeQuery({
        ("gen-mixed", 0): {"parameters": {"positive_prompt": "hello"}, "models": []},
    })
    history = FakeHistoryFacade(generations, query)
    media_store = FakeMediaStore({("gen-mixed", "a.png"): path_image, ("gen-mixed", "clip.mp4"): video_path})
    container = FakeContainer(history, media_store)

    client = client_factory(container)

    response = client.post("/api/plugins/civitai-provider/export-zip", json={"generation_ids": ["gen-mixed"]})
    assert response.status_code == 200

    with ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {"gen-mixe_0.png", "export-report.txt"}
        report = archive.read("export-report.txt").decode()
        assert "gen-mixed [1]" in report
        assert "not an image" in report


def test_export_zip_continues_after_per_file_failure(png_dir, png_bytes_factory, client_factory):
    path_a = _write_png(png_dir, "a.png", png_bytes_factory)
    path_b = _write_png(png_dir, "b.png", png_bytes_factory)

    generations = {
        "gen-flaky": {
            "files": [_file(0, "IMAGE", path_a), _file(1, "IMAGE", path_b)],
            "form_data": {},
        },
    }
    query = FakeQuery(
        {
            ("gen-flaky", 1): {"parameters": {"positive_prompt": "survives"}, "models": []},
        },
        raise_for={("gen-flaky", 0): RuntimeError("boom")},
    )
    history = FakeHistoryFacade(generations, query)
    media_store = FakeMediaStore({("gen-flaky", "a.png"): path_a, ("gen-flaky", "b.png"): path_b})
    container = FakeContainer(history, media_store)

    client = client_factory(container)

    response = client.post("/api/plugins/civitai-provider/export-zip", json={"generation_ids": ["gen-flaky"]})
    assert response.status_code == 200

    with ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {"gen-flak_1.png", "export-report.txt"}
        report = archive.read("export-report.txt").decode()
        assert "gen-flaky [0]" in report
        assert "internal error" in report
