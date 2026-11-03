import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.features.downloads.queue import DownloadQueue
from src.features.models.routes import ModelController, build_router
from src.platform.filesystem.model_types import type_to_directory
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User
from tests.fixtures.model_roots import make_roots

RESOLVED_URL = "https://huggingface.co/ZhengPeng7/BiRefNet/resolve/main/model.safetensors"
BIREFNET_REF = '{"repo": "ZhengPeng7/BiRefNet", "file": "model.safetensors", "revision": "main"}'
BIREFNET_SHA = "9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154"


class _FakeProviderRegistry:
    def __init__(self, url):
        self.url = url
        self.calls = []

    async def get_download_url(self, provider, model_id, version_id):
        self.calls.append((provider, model_id, version_id))
        return self.url


@pytest.fixture
def depot(tmp_path):
    return tmp_path / "depot"


@pytest.fixture
def repo():
    repository = Mock()
    repository.create.side_effect = lambda download: download
    return repository


@pytest.fixture
def providers(monkeypatch):
    registry = _FakeProviderRegistry(RESOLVED_URL)
    monkeypatch.setattr(
        "src.features.providers.registry.ensure_providers_discovered",
        AsyncMock(return_value=registry),
    )
    return registry


@pytest.fixture
def client(repo, depot, tmp_path, providers):
    plugin_registry = Mock()
    context = Mock()
    context.data = {}
    plugin_registry.execute_hook.return_value = (context, [])

    settings = Mock()
    settings.get_setting.side_effect = lambda key, default=None: {
        "models_dir": str(depot),
        "file_storage_directory": str(tmp_path / "media"),
    }.get(key, default)

    queue = DownloadQueue(
        download_repository=repo,
        plugin_registry=plugin_registry,
        settings=settings,
        connection_hub=AsyncMock(),
        resolver=make_roots(tmp_path, home_dir=depot),
    )
    queue.worker = Mock()
    queue.worker.enqueue = AsyncMock()
    queue.worker.get_queue_position.return_value = 0

    user = Mock(spec=User)
    user.id = "admin-1"
    user.account_type = AccountType.ADMIN

    container = Mock()
    container.model_controller = ModelController(Mock(), Mock(), queue)
    app = FastAPI()
    app.include_router(build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _body(name, **overrides):
    body = {
        "name": name,
        "model_type": "detection_segm",
        "provider": "huggingface",
        "ref": BIREFNET_REF,
        "sha256": BIREFNET_SHA,
    }
    body.update(overrides)
    return body


async def _queue(client, repo, body):
    async with client:
        response = await client.post("/api/models/downloads", json=body)
    assert response.status_code == 200
    assert response.json()["success"] is True
    repo.create.assert_called_once()
    return repo.create.call_args.args[0]


def _type_dir(depot):
    return depot / type_to_directory()["detection_segm"]


@pytest.mark.asyncio
async def test_a_recommendation_named_like_a_file_downloads_under_that_name(client, repo, depot, providers):
    download = await _queue(client, repo, _body("BiRefNet-general.safetensors"))

    assert download.filename == "BiRefNet-general.safetensors"
    assert Path(download.destination_path) == _type_dir(depot) / "BiRefNet-general.safetensors"
    assert download.filename_supplied is True
    assert download.url == RESOLVED_URL
    assert download.checksum_sha256 == BIREFNET_SHA
    assert providers.calls == [("huggingface", "ZhengPeng7/BiRefNet", "main@model.safetensors")]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["BiRefNet-general", "FLUX.1-dev", "RealESRGAN 4x+ Anime", "weights.gguf"])
async def test_a_name_without_a_recommendation_extension_keeps_the_url_name(client, repo, depot, name):
    download = await _queue(client, repo, _body(name))

    assert download.filename == "model.safetensors"
    assert Path(download.destination_path) == _type_dir(depot) / "model.safetensors"
    assert download.filename_supplied is False


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", [".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".SafeTensors"])
async def test_every_recommendation_extension_names_the_file(client, repo, extension):
    download = await _queue(client, repo, _body(f"Matte-Model{extension}"))

    assert download.filename == f"Matte-Model{extension}"
    assert download.filename_supplied is True


@pytest.mark.asyncio
async def test_a_provider_less_link_recommendation_is_named_too(client, repo, depot):
    body = {
        "name": "Sharp-Upscaler.pth",
        "model_type": "detection_segm",
        "link": "https://example.com/files/download?id=77",
    }
    download = await _queue(client, repo, body)

    assert download.filename == "Sharp-Upscaler.pth"
    assert Path(download.destination_path) == _type_dir(depot) / "Sharp-Upscaler.pth"


@pytest.mark.asyncio
async def test_a_link_recommendation_keeps_its_name_url_and_checksum(client, repo, depot, providers):
    body = {
        "name": "BiRefNet-general.safetensors",
        "model_type": "detection_segm",
        "link": RESOLVED_URL,
        "sha256": BIREFNET_SHA,
    }
    download = await _queue(client, repo, body)

    assert download.filename == "BiRefNet-general.safetensors"
    assert Path(download.destination_path) == _type_dir(depot) / "BiRefNet-general.safetensors"
    assert download.filename_supplied is True
    assert download.url == RESOLVED_URL
    assert download.checksum_sha256 == BIREFNET_SHA
    assert providers.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../escape.safetensors", "nested/matte.safetensors", "/abs/matte.safetensors", "..\\escape.safetensors"])
async def test_an_unsafe_name_never_leaves_the_type_directory(client, repo, depot, name):
    download = await _queue(client, repo, _body(name))

    assert download.filename == "model.safetensors"
    assert Path(download.destination_path) == _type_dir(depot) / "model.safetensors"
    assert download.filename_supplied is False


@pytest.mark.asyncio
async def test_a_name_longer_than_the_filesystem_allows_is_cut_and_keeps_its_extension(client, repo, depot):
    download = await _queue(client, repo, _body("x" * 400 + ".safetensors"))

    assert download.filename.startswith("x" * 100)
    assert download.filename.endswith(".safetensors")
    assert len(download.filename.encode("utf-8")) + len(".part") <= 255
    assert Path(download.destination_path) == _type_dir(depot) / download.filename


@pytest.mark.asyncio
async def test_windows_reserved_characters_in_the_name_are_replaced(client, repo):
    download = await _queue(client, repo, _body('Matte: "v2".safetensors'))

    assert download.filename == "Matte_ _v2_.safetensors"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gated, gated_repo",
    [(True, "org/name"), (False, None)],
)
async def test_the_status_reports_whether_a_failure_was_a_gated_repo(client, repo, gated, gated_repo):
    from src.features.downloads.models import Download, DownloadStatus

    repo.get_by_id.return_value = Download(
        id="d1",
        url=RESOLVED_URL,
        filename="model.safetensors",
        destination_path="/models/model.safetensors",
        status=DownloadStatus.FAILED,
        error_message="Access denied (HTTP 403)",
        gated=gated,
        gated_repo=gated_repo,
    )

    async with client:
        response = await client.get("/api/models/downloads/d1")

    data = response.json()["data"]
    assert data["status"] == "failed"
    assert data["gated"] is gated
    assert data["gated_repo"] == gated_repo
