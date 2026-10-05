from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.media_types import MediaTypeResolver
from src.features.media.routes import MediaController
from src.features.media.store import MediaStore
from src.platform.settings.settings import Settings

VIDEO_BYTES = bytes(range(256)) * 8


@pytest.fixture
def preset_dir(tmp_path):
    root = tmp_path / "preset"
    (root / "public").mkdir(parents=True)
    (root / "public" / "cover.webm").write_bytes(VIDEO_BYTES)
    (root / "public" / "clip.mp4").write_bytes(VIDEO_BYTES)
    (root / "preset.yml").write_text("id: vid\n")
    return root


@pytest.fixture
def client(preset_dir):
    settings = Mock(spec=Settings)
    settings.get_file_storage_directory.return_value = str(preset_dir / "storage")
    loader = SimpleNamespace(presets=[SimpleNamespace(id="vid", path=str(preset_dir))])
    resolver = FilePathResolver(settings, loader)
    store = MediaStore(
        file_resolver=resolver,
        image_processor=Mock(spec=ImageProcessor),
        media_type_resolver=MediaTypeResolver(),
        file_repository=Mock(),
        generation_repository=Mock(),
        settings=settings,
        file_service=Mock(),
        plugin_registry=Mock(),
        upload_repository=Mock(),
    )
    controller = MediaController(store, Mock())
    router = APIRouter(prefix="/api/media")

    @router.get("/presets/{preset_id}/{file_path:path}")
    async def serve(preset_id: str, file_path: str, request: Request, size: str = None):
        return await controller.serve_preset_file(preset_id, file_path, size, request)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestPresetVideoCoverServing:
    def test_webm_is_served_with_video_mime(self, client):
        response = client.get("/api/media/presets/vid/public/cover.webm")
        assert response.status_code == 200
        assert response.headers["content-type"] == "video/webm"
        assert response.headers["accept-ranges"] == "bytes"
        assert response.content == VIDEO_BYTES

    def test_mp4_is_served_with_video_mime(self, client):
        response = client.get("/api/media/presets/vid/public/clip.mp4")
        assert response.status_code == 200
        assert response.headers["content-type"] == "video/mp4"

    def test_range_request_returns_partial_content(self, client):
        response = client.get(
            "/api/media/presets/vid/public/cover.webm", headers={"Range": "bytes=10-19"}
        )
        assert response.status_code == 206
        assert response.content == VIDEO_BYTES[10:20]
        assert response.headers["content-range"] == f"bytes 10-19/{len(VIDEO_BYTES)}"
        assert response.headers["content-length"] == "10"

    def test_open_ended_range_runs_to_end_of_file(self, client):
        response = client.get(
            "/api/media/presets/vid/public/cover.webm", headers={"Range": "bytes=2040-"}
        )
        assert response.status_code == 206
        assert response.content == VIDEO_BYTES[2040:]

    def test_video_outside_public_is_not_served(self, client, preset_dir):
        (preset_dir / "secret.webm").write_bytes(VIDEO_BYTES)
        assert client.get("/api/media/presets/vid/secret.webm").status_code == 404

    def test_encoded_traversal_to_a_video_is_not_served(self, client, preset_dir):
        (preset_dir.parent / "outside.webm").write_bytes(VIDEO_BYTES)
        response = client.get(f"/api/media/presets/vid/public/..%2f../outside.webm")
        assert response.status_code == 404

    def test_resolver_rejects_an_unnormalised_traversal(self, preset_dir):
        settings = Mock(spec=Settings)
        loader = SimpleNamespace(presets=[SimpleNamespace(id="vid", path=str(preset_dir))])
        (preset_dir.parent / "outside.webm").write_bytes(VIDEO_BYTES)
        with pytest.raises(ValueError):
            FilePathResolver(settings, loader).resolve_preset_file("vid", "public/../../outside.webm")

    def test_non_media_extension_in_public_is_not_served(self, client, preset_dir):
        (preset_dir / "public" / "cover.yml").write_bytes(VIDEO_BYTES)
        assert client.get("/api/media/presets/vid/public/cover.yml").status_code == 404
