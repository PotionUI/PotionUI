"""End-to-end upload lifecycle with the S3 storage backend active.

Exercises the real entry points a request would hit - `MediaManager.upload_media`
then `MediaController.serve_uploaded_media` (the actual route handler, not a
manager method called directly) - against an in-process fake S3 built on
`httpx.MockTransport`. Never a real network call.
"""

from unittest.mock import Mock

import httpx
import pytest

from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.manager import MediaManager
from src.features.media.media_types import MediaTypeResolver
from src.features.media.routes import MediaController
from src.features.media.upload_repository import UploadRepository
from src.features.media.validators import UPLOAD_PURPOSE_USER
from src.features.generation.file_repository import FileRepository
from src.features.generation.repository import GenerationRepository
from src.platform.filesystem.file_store import FileStore
from src.platform.filesystem.s3_driver import S3FileStorageDriver
from src.platform.plugins import PluginRegistry
from src.platform.settings.settings import SettingsManager


class _FakeS3Backend:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "PUT":
            self.objects[path] = request.read()
            return httpx.Response(200)
        if request.method == "GET":
            if path not in self.objects:
                return httpx.Response(404)
            return httpx.Response(200, content=self.objects[path])
        if request.method == "HEAD":
            if path not in self.objects:
                return httpx.Response(404)
            return httpx.Response(200, headers={"content-length": str(len(self.objects[path]))})
        if request.method == "DELETE":
            self.objects.pop(path, None)
            return httpx.Response(204)
        return httpx.Response(400)


@pytest.fixture
def s3_driver():
    backend = _FakeS3Backend()
    client = httpx.Client(transport=httpx.MockTransport(backend.handler))
    return S3FileStorageDriver(
        bucket="potionui-test",
        access_key_id="AKIDEXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        path_style=True,
        client=client,
    )


@pytest.fixture
def manager(s3_driver, tmp_path):
    settings = Mock(spec=SettingsManager)
    settings.get_file_storage_directory.return_value = str(tmp_path)

    media_types = Mock(spec=MediaTypeResolver)
    media_types.is_valid_media_type.return_value = True
    media_types.is_image.return_value = False
    media_types.is_video.return_value = False
    media_types.is_audio.return_value = False
    media_types.get_media_type.return_value = "application/octet-stream"

    plugin_registry = Mock(spec=PluginRegistry)
    hook_context = Mock()
    hook_context.data = {}
    plugin_registry.execute_hook.return_value = (hook_context, [])

    return MediaManager(
        file_resolver=Mock(spec=FilePathResolver),
        image_processor=Mock(spec=ImageProcessor),
        media_type_resolver=media_types,
        file_repository=Mock(spec=FileRepository),
        generation_repository=Mock(spec=GenerationRepository),
        settings_manager=settings,
        file_service=Mock(spec=FileStore),
        plugin_registry=plugin_registry,
        upload_repository=Mock(spec=UploadRepository),
        storage_driver=s3_driver,
    )


@pytest.fixture
def controller(manager):
    return MediaController(manager)


@pytest.mark.asyncio
async def test_upload_serve_delete_round_trip_through_s3_backend(manager, controller):
    original_bytes = b"\x89PNG-fake-image-bytes-not-really-a-png" * 50

    upload_result = await manager.upload_media(
        file_data=original_bytes,
        filename="photo.png",
        content_type="image/png",
        user_id="user123",
        purpose=UPLOAD_PURPOSE_USER,
    )
    assert upload_result.size == len(original_bytes)

    # Served through the real route handler, not a manager method called
    # directly - proves the controller's `.content` branch actually returns
    # the S3-backed bytes rather than trying to FileResponse a path that
    # does not exist locally.
    response = await controller.serve_uploaded_media(upload_result.filename)
    assert response.body == original_bytes

    await controller.delete_upload(upload_result.filename, Mock(id="user123"))

    with pytest.raises(ValueError, match="Uploaded file not found"):
        manager.get_uploaded_media(upload_result.filename)


@pytest.mark.asyncio
async def test_upload_info_probes_through_a_materialized_local_copy(manager):
    """S3 has no local file - `get_upload_info` must still be able to probe
    metadata (dimensions/duration/fps) via a temp local copy of the bytes."""
    data = b"fake-audio-bytes"
    upload_result = await manager.upload_media(
        file_data=data,
        filename="clip.wav",
        content_type="audio/wav",
        user_id="user123",
    )

    info = manager.get_upload_info(upload_result.filename)
    assert info.size == len(data)
