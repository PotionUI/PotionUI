"""Media index admin routes: response shapes over a mocked manager."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType, User

from src.features.media_index.routes import MediaIndexController, build_router
from src.features.downloads.models import Download, DownloadStatus, DownloadType


@pytest.fixture
def manager():
    manager = MagicMock()
    manager.status.return_value = {
        "queue": {"tags": {"pending": 2}},
        "tagged_files": 5,
        "provenance": "smilingwolf-wd-vit-tagger-v3",
    }
    manager.backfill.return_value = 3
    manager.retag_stale.return_value = 0
    manager.process_pending.return_value = {"processed": 2, "failed": 0}
    manager.repository.queue_counts.return_value = {"tags": {"pending": 0}}
    return manager


@pytest.fixture
def settings(tmp_path):
    settings = MagicMock()
    values = {
        "media_tagger_model": "SmilingWolf/wd-vit-tagger-v3",
        "media_vision_model": "google/siglip-base-patch16-224",
    }
    settings.get_setting.side_effect = lambda key, default=None: values.get(key, default)
    settings.get_models_dir.return_value = str(tmp_path)
    return settings


@pytest.fixture
def download_queue():
    dm = MagicMock()
    dm.find_active_download_for_repo.return_value = None
    return dm


@pytest.fixture
def client(manager, settings, download_queue):
    container = MagicMock()
    container.media_index_controller = MediaIndexController(manager)
    container.settings = settings
    container.download_queue = download_queue
    app = FastAPI()
    app.include_router(build_router(container))

    async def _admin():
        return User(
            id="a1", username="admin", email="a@example.com",
            password_hash="h", account_type=AccountType.ADMIN,
        )

    app.dependency_overrides[get_current_active_user] = _admin
    return TestClient(app)


def test_models_status_reports_absent_weights_for_saved_models(client, tmp_path):
    resp = client.get("/api/media-index/models-status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tagger"]["present"] is False
    assert data["tagger"]["path"] == str(tmp_path / "taggers" / "smilingwolf-wd-vit-tagger-v3")
    assert data["tagger"]["active_download"] is None
    assert data["vision"]["present"] is False
    assert data["vision"]["path"] == str(tmp_path / "vision_embeddings" / "google-siglip-base-patch16-224")
    assert data["vision"]["active_download"] is None


def test_models_status_reports_active_download_for_running_tagger_fetch(client, download_queue):
    """A reloading client must see an in-flight tagger fetch through this
    endpoint alone - no page-local downloadId->kind map involved."""
    active = Download(
        id="dl-tagger-1",
        type=DownloadType.HF_REPO,
        url="https://huggingface.co/SmilingWolf/wd-vit-tagger-v3",
        destination_path="/models/taggers/smilingwolf-wd-vit-tagger-v3",
        filename="SmilingWolf/wd-vit-tagger-v3",
        status=DownloadStatus.DOWNLOADING,
        progress=0.5,
        repo_id="SmilingWolf/wd-vit-tagger-v3",
    )
    download_queue.find_active_download_for_repo.side_effect = (
        lambda repo_id: active if repo_id == "SmilingWolf/wd-vit-tagger-v3" else None
    )

    resp = client.get("/api/media-index/models-status")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tagger"]["active_download"]["id"] == "dl-tagger-1"
    assert data["tagger"]["active_download"]["status"] == "downloading"
    assert data["vision"]["active_download"] is None


def test_models_status_reports_active_download_for_queued_vision_fetch(client, download_queue):
    active = Download(
        id="dl-vision-1",
        type=DownloadType.HF_REPO,
        url="https://huggingface.co/google/siglip-base-patch16-224",
        destination_path="/models/vision_embeddings/google-siglip-base-patch16-224",
        filename="google/siglip-base-patch16-224",
        status=DownloadStatus.PENDING,
        repo_id="google/siglip-base-patch16-224",
    )
    download_queue.find_active_download_for_repo.side_effect = (
        lambda repo_id: active if repo_id == "google/siglip-base-patch16-224" else None
    )

    resp = client.get("/api/media-index/models-status")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["vision"]["active_download"]["id"] == "dl-vision-1"
    assert data["vision"]["active_download"]["status"] == "pending"
    assert data["tagger"]["active_download"] is None


def test_models_status_reports_loaded_when_active_providers_match(client, manager):
    """`loaded` is residency (in memory), never inferred from `present`
    (on-disk) - both models load through ModelLifecycle and can be
    evicted while still on disk."""
    manager.tagger_provider = SimpleNamespace(
        model_name="SmilingWolf/wd-vit-tagger-v3", is_loaded=lambda: True
    )
    manager.vision_embedder = SimpleNamespace(
        model_name="google/siglip-base-patch16-224", is_loaded=lambda: False
    )

    resp = client.get("/api/media-index/models-status")

    data = resp.json()["data"]
    assert data["tagger"]["loaded"] is True
    assert data["vision"]["loaded"] is False


def test_models_status_reports_not_loaded_for_an_unsaved_override_model(client, manager):
    """The active provider instance's residency answers for its own model
    only - querying a different (unsaved) model id must not borrow it."""
    manager.tagger_provider = SimpleNamespace(
        model_name="SmilingWolf/wd-vit-tagger-v3", is_loaded=lambda: True
    )

    resp = client.get(
        "/api/media-index/models-status",
        params={"tagger_model": "SmilingWolf/wd-swinv2-tagger-v3"},
    )

    assert resp.json()["data"]["tagger"]["loaded"] is False


def test_models_status_honors_unsaved_query_overrides(client, tmp_path):
    resp = client.get(
        "/api/media-index/models-status",
        params={"tagger_model": "SmilingWolf/wd-swinv2-tagger-v3"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tagger"]["path"] == str(tmp_path / "taggers" / "smilingwolf-wd-swinv2-tagger-v3")
    # Vision falls back to the saved setting when not overridden.
    assert data["vision"]["path"] == str(tmp_path / "vision_embeddings" / "google-siglip-base-patch16-224")


def test_status_payload(client):
    resp = client.get("/api/media-index/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["queue"]["tags"]["pending"] == 2
    assert data["tagged_files"] == 5
    assert data["provenance"] == "smilingwolf-wd-vit-tagger-v3"


def test_backfill_without_retag(client, manager):
    resp = client.post("/api/media-index/backfill", json={})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["enqueued"] == 3
    assert data["retag_requeued"] == 0
    manager.retag_stale.assert_not_called()


def test_backfill_with_retag(client, manager):
    manager.retag_stale.return_value = 4
    resp = client.post("/api/media-index/backfill", json={"retag_stale": True})
    assert resp.json()["data"]["retag_requeued"] == 4
    manager.retag_stale.assert_called_once()


def test_process_drains_batch(client, manager):
    resp = client.post(
        "/api/media-index/process", json={"pass_type": "tags", "batch_size": 5}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["processed"] == 2
    manager.process_pending.assert_called_once_with("tags", 5)


def test_process_unknown_pass_type_is_client_error(client, manager):
    manager.process_pending.side_effect = ValueError("Unknown media index pass type: x")
    resp = client.post("/api/media-index/process", json={"pass_type": "x"})
    assert resp.json()["success"] is False
    assert resp.json()["error"] == "media_index_process_failed"
