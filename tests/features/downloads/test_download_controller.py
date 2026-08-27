"""
Tests for the core download HTTP API endpoints.

Drives the real /api/downloads router (built from a mock container) through a
TestClient, with the auth dependency overridden to an admin.
"""

import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.downloads.models import Download, DownloadType, DownloadStatus, DownloadSettings
from src.features.downloads.exceptions import (
    DownloadNotFoundException,
    DownloadQueueException,
    DownloadOperationException,
    InvalidStatusException,
    InvalidTypeException,
)


@pytest.fixture
def mock_download_manager():
    """Mock DownloadQueue"""
    manager = Mock()
    manager.list_downloads = Mock(return_value={
        'downloads': [],
        'counts': {},
        'total': 0
    })
    manager.get_download = Mock(return_value=None)
    manager.queue_model_download = AsyncMock()
    manager.queue_media_download = AsyncMock()
    manager.queue_batch_downloads = AsyncMock()
    manager.pause_download = AsyncMock()
    manager.resume_download = AsyncMock()
    manager.cancel_download = AsyncMock()
    manager.retry_download = AsyncMock()
    manager.delete_download = AsyncMock()
    manager.clear_completed = Mock(return_value=0)
    manager.clear_cancelled = Mock(return_value=0)
    manager.get_settings = Mock(return_value=DownloadSettings())
    manager.update_settings = Mock()
    manager.start = AsyncMock()
    return manager


@pytest.fixture
def mock_admin_user():
    """Mock admin user for auth dependency"""
    from src.platform.security.user import User, AccountType
    user = Mock(spec=User)
    user.id = "admin-user-1"
    user.account_type = AccountType.ADMIN
    return user


@pytest.fixture
def sample_download():
    """Sample download for testing"""
    return Download(
        id="test-download-1",
        type=DownloadType.MODEL,
        url="https://example.com/model.safetensors",
        destination_path="/models/model.safetensors",
        filename="model.safetensors",
        status=DownloadStatus.PENDING,
        progress=0.0,
        total_bytes=1000000,
        downloaded_bytes=0,
        created_at=datetime(2024, 1, 1, 12, 0, 0)
    )


@pytest.fixture
def sample_download_settings():
    """Sample download settings for testing"""
    return DownloadSettings(
        max_concurrent_downloads=2,
        auto_retry_failed=True,
        max_retries=3,
        chunk_size_kb=1024,
        verify_checksum=True,
        default_model_directory="models",
        default_media_directory="storage/media"
    )


@pytest.fixture
def mock_download_repository():
    """Mock DownloadRepository"""
    return Mock()


@pytest.fixture
def client(mock_download_manager, mock_download_repository, mock_admin_user):
    """Create a FastAPI TestClient over the real router with mocked collaborators."""
    from src.platform.security.current_user import get_current_active_user
    from src.features.downloads.routes import build_router

    container = Mock()
    container.download_queue = mock_download_manager
    container.download_repository = mock_download_repository

    app = FastAPI()
    app.include_router(build_router(container))

    # Override auth dependency (the real admin gate still runs on top of it)
    app.dependency_overrides[get_current_active_user] = lambda: mock_admin_user

    yield TestClient(app)

    app.dependency_overrides.clear()


# ========== List Downloads Tests ==========

def test_list_downloads_success(client, mock_download_manager, sample_download):
    """Test listing all downloads successfully"""
    mock_download_manager.list_downloads.return_value = {
        'downloads': [sample_download.to_dict()],
        'counts': {'pending': 1},
        'total': 1
    }

    response = client.get("/api/downloads")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert len(data['data']['downloads']) == 1
    assert data['data']['downloads'][0]['id'] == "test-download-1"


def test_list_downloads_with_status_filter(client, mock_download_manager):
    """Test listing downloads with status filter"""
    mock_download_manager.list_downloads.return_value = {
        'downloads': [],
        'counts': {},
        'total': 0
    }

    response = client.get("/api/downloads?status=pending")

    assert response.status_code == 200
    mock_download_manager.list_downloads.assert_called_once()
    call_kwargs = mock_download_manager.list_downloads.call_args[1]
    assert call_kwargs['status'] == "pending"


def test_list_downloads_with_type_filter(client, mock_download_manager):
    """Test listing downloads with type filter"""
    mock_download_manager.list_downloads.return_value = {
        'downloads': [],
        'counts': {},
        'total': 0
    }

    response = client.get("/api/downloads?type=model")

    assert response.status_code == 200
    call_kwargs = mock_download_manager.list_downloads.call_args[1]
    assert call_kwargs['download_type'] == "model"


def test_list_downloads_invalid_status(client, mock_download_manager):
    """Test list downloads with invalid status returns error"""
    mock_download_manager.list_downloads.side_effect = InvalidStatusException("Invalid status: bad")

    response = client.get("/api/downloads?status=bad")

    assert response.status_code == 400


def test_list_downloads_invalid_type(client, mock_download_manager):
    """Test list downloads with invalid type returns error"""
    mock_download_manager.list_downloads.side_effect = InvalidTypeException("Invalid type: bad")

    response = client.get("/api/downloads?type=bad")

    assert response.status_code == 400


# ========== Get Download Tests ==========

def test_get_download_success(client, mock_download_manager, sample_download):
    """Test getting a single download successfully"""
    mock_download_manager.get_download.return_value = sample_download

    response = client.get("/api/downloads/test-download-1")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert data['data']['id'] == "test-download-1"
    assert data['data']['filename'] == "model.safetensors"


def test_get_download_not_found(client, mock_download_manager):
    """Test getting a non-existent download returns error"""
    mock_download_manager.get_download.side_effect = DownloadNotFoundException("Not found")

    response = client.get("/api/downloads/non-existent")

    assert response.status_code == 404


# ========== Queue Model Download Tests ==========

def test_queue_model_download_success(client, mock_download_manager, sample_download):
    """Test queuing a model download successfully"""
    mock_download_manager.queue_model_download.return_value = sample_download

    response = client.post(
        "/api/downloads/model",
        json={
            "url": "https://example.com/model.safetensors",
            "destination_dir": "models",
            "tags": ["sdxl", "checkpoint"]
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert data['data']['id'] == "test-download-1"
    mock_download_manager.queue_model_download.assert_called_once()


def test_queue_model_download_with_checksum(client, mock_download_manager, sample_download):
    """Test queuing a model download with checksum verification"""
    mock_download_manager.queue_model_download.return_value = sample_download

    response = client.post(
        "/api/downloads/model",
        json={
            "url": "https://example.com/model.safetensors",
            "checksum_sha256": "abc123def456"
        }
    )

    assert response.status_code == 200
    call_kwargs = mock_download_manager.queue_model_download.call_args[1]
    assert call_kwargs['checksum_sha256'] == "abc123def456"


def test_queue_model_download_blocked(client, mock_download_manager):
    """Test queue model download blocked by hook"""
    mock_download_manager.queue_model_download.side_effect = DownloadQueueException("Blocked by plugin")

    response = client.post(
        "/api/downloads/model",
        json={"url": "https://example.com/model.safetensors"}
    )

    assert response.status_code == 400


# ========== Queue Media Download Tests ==========

def test_queue_media_download_success(client, mock_download_manager, sample_download):
    """Test queuing a media download successfully"""
    sample_download.type = DownloadType.MEDIA
    mock_download_manager.queue_media_download.return_value = sample_download

    response = client.post(
        "/api/downloads/media",
        json={
            "url": "https://example.com/image.png",
            "destination_dir": "storage/media"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    mock_download_manager.queue_media_download.assert_called_once()


# ========== Queue Batch Downloads Tests ==========

def test_queue_batch_downloads_success(client, mock_download_manager):
    """Test queuing batch downloads successfully"""
    mock_download_manager.queue_batch_downloads.return_value = {
        'queued': [{'id': 'dl-1'}, {'id': 'dl-2'}],
        'errors': [],
        'total_queued': 2,
        'total_errors': 0
    }

    response = client.post(
        "/api/downloads/batch",
        json={
            "urls": ["https://example.com/a.png", "https://example.com/b.png"]
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert data['data']['total_queued'] == 2
    assert data['data']['total_errors'] == 0


# ========== Pause Download Tests ==========

def test_pause_download_success(client, mock_download_manager, sample_download):
    """Test pausing a download successfully"""
    sample_download.status = DownloadStatus.PAUSED
    mock_download_manager.pause_download.return_value = sample_download

    response = client.post("/api/downloads/test-download-1/pause")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert "paused" in data['message'].lower()


def test_pause_download_not_found(client, mock_download_manager):
    """Test pausing a non-existent download"""
    mock_download_manager.pause_download.side_effect = DownloadNotFoundException("Not found")

    response = client.post("/api/downloads/non-existent/pause")

    assert response.status_code == 404


def test_pause_download_operation_failed(client, mock_download_manager):
    """Test pausing when operation fails"""
    mock_download_manager.pause_download.side_effect = DownloadOperationException("Not active")

    response = client.post("/api/downloads/test-download-1/pause")

    assert response.status_code == 400


# ========== Resume Download Tests ==========

def test_resume_download_success(client, mock_download_manager, sample_download):
    """Test resuming a download successfully"""
    sample_download.status = DownloadStatus.DOWNLOADING
    mock_download_manager.resume_download.return_value = sample_download

    response = client.post("/api/downloads/test-download-1/resume")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert "resumed" in data['message'].lower()


def test_resume_download_operation_failed(client, mock_download_manager):
    """Test resuming when operation fails"""
    mock_download_manager.resume_download.side_effect = DownloadOperationException("Not paused")

    response = client.post("/api/downloads/test-download-1/resume")

    assert response.status_code == 400


# ========== Cancel Download Tests ==========

def test_cancel_download_success(client, mock_download_manager, sample_download):
    """Test cancelling a download successfully"""
    sample_download.status = DownloadStatus.CANCELLED
    mock_download_manager.cancel_download.return_value = sample_download

    response = client.post("/api/downloads/test-download-1/cancel")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert "cancelled" in data['message'].lower()


def test_cancel_download_operation_failed(client, mock_download_manager):
    """Test cancelling when operation fails"""
    mock_download_manager.cancel_download.side_effect = DownloadOperationException("Already completed")

    response = client.post("/api/downloads/test-download-1/cancel")

    assert response.status_code == 400


# ========== Retry Download Tests ==========

def test_retry_download_success(client, mock_download_manager, sample_download):
    """Test retrying a failed download successfully"""
    sample_download.status = DownloadStatus.PENDING
    mock_download_manager.retry_download.return_value = sample_download

    response = client.post("/api/downloads/test-download-1/retry")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True


def test_retry_download_operation_failed(client, mock_download_manager):
    """Test retrying when operation fails"""
    mock_download_manager.retry_download.side_effect = DownloadOperationException("Not failed")

    response = client.post("/api/downloads/test-download-1/retry")

    assert response.status_code == 400


# ========== Delete Download Tests ==========

def test_delete_download_success(client, mock_download_manager):
    """Test deleting a download record successfully"""
    mock_download_manager.delete_download.return_value = None

    response = client.delete("/api/downloads/test-download-1")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert "deleted" in data['message'].lower()


def test_delete_download_not_found(client, mock_download_manager):
    """Test deleting a non-existent download"""
    mock_download_manager.delete_download.side_effect = DownloadNotFoundException("Not found")

    response = client.delete("/api/downloads/non-existent")

    assert response.status_code == 404


# ========== Clear Completed Tests ==========

def test_clear_completed_success(client, mock_download_manager):
    """Test clearing completed downloads successfully"""
    mock_download_manager.clear_completed.return_value = 5

    response = client.post("/api/downloads/clear-completed")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert "5" in data['message']


def test_clear_completed_blocked(client, mock_download_manager):
    """Test clearing completed when blocked"""
    mock_download_manager.clear_completed.side_effect = DownloadOperationException("Blocked")

    response = client.post("/api/downloads/clear-completed")

    assert response.status_code == 400


# ========== Clear Cancelled Tests ==========

def test_clear_cancelled_success(client, mock_download_manager):
    """Test clearing cancelled downloads successfully"""
    mock_download_manager.clear_cancelled.return_value = 3

    response = client.post("/api/downloads/clear-cancelled")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert "3" in data['message']


# ========== Settings Tests ==========

def test_get_settings_success(client, mock_download_manager, sample_download_settings):
    """Test getting download settings successfully"""
    mock_download_manager.get_settings.return_value = sample_download_settings

    response = client.get("/api/downloads/settings")

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    assert data['data']['max_concurrent_downloads'] == 2
    assert data['data']['auto_retry_failed'] is True


def test_update_settings_success(client, mock_download_manager, sample_download_settings):
    """Test updating download settings successfully"""
    mock_download_manager.get_settings.return_value = sample_download_settings

    response = client.put(
        "/api/downloads/settings",
        json={
            "max_concurrent_downloads": 3,
            "auto_retry_failed": False
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    mock_download_manager.update_settings.assert_called_once()


# ========== HF Repo Download Tests ==========

def test_queue_hf_repo_download_success(client, mock_download_manager, sample_download):
    """Queue a grouped Hugging Face repo download."""
    sample_download.type = DownloadType.HF_REPO
    sample_download.repo_id = "org/tiny-model"
    mock_download_manager.queue_hf_repo_download = AsyncMock(return_value=sample_download)

    response = client.post(
        "/api/downloads/hf-repo",
        json={"repo_id": "org/tiny-model", "allow_patterns": ["*.safetensors"]}
    )

    assert response.status_code == 200
    data = response.json()
    assert data['success'] is True
    call_kwargs = mock_download_manager.queue_hf_repo_download.call_args[1]
    assert call_kwargs['repo_id'] == "org/tiny-model"
    assert call_kwargs['allow_patterns'] == ["*.safetensors"]


def test_queue_hf_repo_download_enumeration_failure(client, mock_download_manager):
    """Enumeration/queue failures surface as 400."""
    mock_download_manager.queue_hf_repo_download = AsyncMock(
        side_effect=DownloadQueueException("Could not enumerate")
    )

    response = client.post("/api/downloads/hf-repo", json={"repo_id": "org/missing"})

    assert response.status_code == 400


def test_get_grouped_download_includes_children(client, mock_download_manager, mock_download_repository, sample_download):
    """A grouped parent's GET carries its per-file children."""
    sample_download.type = DownloadType.HF_REPO
    mock_download_manager.get_download.return_value = sample_download
    child = Download(
        id="child-1", type=DownloadType.MODEL, url="https://huggingface.co/org/m/resolve/main/a.bin",
        destination_path="/models/a.bin", filename="a.bin", group_id="test-download-1",
    )
    mock_download_repository.get_children.return_value = [child]

    response = client.get("/api/downloads/test-download-1")

    assert response.status_code == 200
    data = response.json()['data']
    assert [c['id'] for c in data['children']] == ["child-1"]
