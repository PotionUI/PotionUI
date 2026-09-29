from unittest.mock import MagicMock

from src.features.backends.records import Backend
from src.features.backends.repository import BackendRepository
from src.features.backends.backend_config import NATIVE_LOCAL_DRIVER
from src.features.models.availability_records import ModelAvailability
from src.features.models.availability_repository import model_availability_repo
from src.features.models.indexing_coordinator import ModelIndexingCoordinator
from src.features.models.native_availability_reconciler import NativeAvailabilityProjector
from tests.features.models.test_indexing_coordinator import FakePluginRegistry, _real_scanner


class _Backend:
    class config:
        driver = NATIVE_LOCAL_DRIVER

    def __init__(self, backend_id):
        self.backend_id = backend_id


class _Registry:
    def __init__(self, backend_id):
        self.backend_id = backend_id

    def get_backends_for_engine(self, engine):
        return [_Backend(self.backend_id)]


def _create_backend():
    return BackendRepository().create(
        Backend(id="", name="Native", engine="native", driver=NATIVE_LOCAL_DRIVER, enabled=True, is_default=False, config={})
    ).id


def _coordinator(scanner, backend_id):
    return ModelIndexingCoordinator(
        model_repository=MagicMock(), plugin_registry=FakePluginRegistry(), scanner=scanner,
        backend_registry=_Registry(backend_id),
        native_availability_projector=NativeAvailabilityProjector(resolver=scanner.resolver),
        spawn=lambda target: target(),
    )


def _listed_for_backend(model_id, backend_id):
    return model_availability_repo.get(model_id, backend_id) is not None


def test_indexing_a_downloaded_file_makes_it_available_on_an_already_indexed_local_backend(tmp_path, mock_db):
    scanner, _, type_dir = _real_scanner(tmp_path)
    (type_dir / "first.safetensors").write_bytes(b"first")
    backend_id = _create_backend()
    coordinator = _coordinator(scanner, backend_id)
    coordinator.start_indexing(trigger="manual")
    assert model_availability_repo.has_any()

    downloaded = type_dir / "downloaded.safetensors"
    downloaded.write_bytes(b"downloaded")
    result = coordinator.index_path(str(downloaded))

    assert result["indexed"] is True
    assert _listed_for_backend(result["model_id"], backend_id)


def test_downloaded_worker_path_end_to_end_makes_the_model_visible(tmp_path, mock_db):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from src.features.downloads.models import Download, DownloadSettings, DownloadStatus, DownloadType
    from src.features.downloads.worker import DownloadWorker

    scanner, _, type_dir = _real_scanner(tmp_path)
    (type_dir / "first.safetensors").write_bytes(b"first")
    backend_id = _create_backend()
    coordinator = _coordinator(scanner, backend_id)
    coordinator.start_indexing(trigger="manual")

    path = type_dir / "fetched.safetensors"
    path.write_bytes(b"fetched")
    download = Download(id="d1", type=DownloadType.MODEL, filename="fetched.safetensors", destination_path=str(path))
    repo = Mock()
    repo.get_by_id.return_value = download
    worker = DownloadWorker(
        settings=DownloadSettings(verify_checksum=False), repo=repo, connection_hub=AsyncMock(),
        provider_registry_factory=lambda: None, backend_registry=_Registry(backend_id),
        local_model_indexer=coordinator.index_path,
    )

    asyncio.run(worker._index_local_download(download))

    from src.features.models.repository import ModelRepository
    model = ModelRepository().get_by_identity("checkpoint", "fetched.safetensors", include_providers=False)
    assert model is not None
    assert _listed_for_backend(model.id, backend_id)
