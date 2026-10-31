import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml

from src.features.downloads.models import DownloadSettings
from src.features.downloads.queue import DownloadQueue
from src.features.downloads.worker import DownloadWorker
from tests.features.downloads.fakes import FakeResponse, FakeSession
from tests.fixtures.model_roots import make_roots

CATALOG = Path(__file__).resolve().parents[3] / "src/std/models/preview_decoders.yml"
ENTRY = next(e for e in yaml.safe_load(CATALOG.read_text())["models"] if e["url"].endswith("/taew2_1.safetensors"))
CHUNKS = (b"tiny", b"decoder", b"weights")
CONTENT = b"".join(CHUNKS)


def _settings(models_dir):
    sm = Mock()
    sm.get_setting.side_effect = lambda key, default=None: {"models_dir": models_dir, "file_storage_directory": None}.get(
        key, default
    )
    return sm


@pytest.fixture
def queue(tmp_path):
    repo = Mock()
    repo.create.side_effect = lambda d: d
    registry = Mock()
    context = Mock()
    context.data = {}
    registry.execute_hook.return_value = (context, [])
    depot = tmp_path / "depot"
    manager = DownloadQueue(
        download_repository=repo,
        plugin_registry=registry,
        settings=_settings(str(depot)),
        connection_hub=AsyncMock(),
        resolver=make_roots(tmp_path, home_dir=depot),
    )
    manager.worker = AsyncMock()
    manager.worker.get_queue_position.return_value = 0
    return manager, depot


def _worker():
    repo = Mock()
    repo.is_destination_claimed.return_value = False
    return DownloadWorker(
        settings=DownloadSettings(chunk_size_kb=1),
        repo=repo,
        connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None,
    )


async def _queued(queue, checksum):
    manager, depot = queue
    with patch.object(manager, "conn", AsyncMock()):
        download = await manager.queue_model_download(
            url=ENTRY["url"], filename="taew2_1.safetensors", model_type="preview_decoder", checksum_sha256=checksum
        )
    return download, depot


def test_the_catalog_entry_is_a_plain_github_url_without_a_provider():
    assert ENTRY["url"].startswith("https://raw.githubusercontent.com/")
    assert "provider" not in ENTRY


@pytest.mark.asyncio
async def test_a_recommendation_link_lands_in_vae_approx_with_its_checksum(queue):
    download, depot = await _queued(queue, ENTRY["sha256"])
    assert Path(download.destination_path).resolve() == (depot / "vae_approx" / "taew2_1.safetensors").resolve()
    assert download.url == ENTRY["url"]
    assert download.checksum_sha256 == ENTRY["sha256"]


@pytest.mark.asyncio
async def test_the_github_url_downloads_and_passes_the_sha256_check(queue, tmp_path, mock_db):
    download, _ = await _queued(queue, hashlib.sha256(CONTENT).hexdigest())
    worker = _worker()
    worker.session = FakeSession(FakeResponse(chunks=CHUNKS))

    assert await worker._download_file(download) is True

    assert worker.session.requests[0][0] == ENTRY["url"]
    assert "Authorization" not in worker.session.requests[0][1]
    assert Path(download.destination_path).read_bytes() == CONTENT
    assert await worker._verify_checksum(download) is True


@pytest.mark.asyncio
async def test_a_github_download_with_the_wrong_bytes_fails_the_sha256_check(queue, mock_db):
    download, _ = await _queued(queue, ENTRY["sha256"])
    worker = _worker()
    worker.session = FakeSession(FakeResponse(chunks=CHUNKS))

    assert await worker._download_file(download) is True
    assert await worker._verify_checksum(download) is False
