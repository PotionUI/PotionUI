"""Request/response models for the download API."""

from typing import Optional, List
from pydantic import BaseModel


# ============ Request DTOs ============

class QueueModelDownloadRequest(BaseModel):
    """Request to queue a model download"""
    url: str
    destination_dir: Optional[str] = None
    model_type: Optional[str] = None
    filename: Optional[str] = None
    tags: Optional[List[str]] = None
    checksum_sha256: Optional[str] = None
    provider_id: Optional[str] = None


class QueueMediaDownloadRequest(BaseModel):
    """Request to queue a media download"""
    url: str
    destination_dir: Optional[str] = None
    filename: Optional[str] = None


class QueueBatchDownloadRequest(BaseModel):
    """Request to queue multiple downloads"""
    urls: List[str]
    destination_dir: Optional[str] = None
    download_type: str = "media"  # "model" or "media"


class QueueHfRepoDownloadRequest(BaseModel):
    """Request to queue a whole Hugging Face repo as one grouped download"""
    repo_id: str
    destination_dir: Optional[str] = None
    revision: Optional[str] = None
    allow_patterns: Optional[List[str]] = None


class UpdateSettingsRequest(BaseModel):
    """Request to update download settings"""
    max_concurrent_downloads: Optional[int] = None
    auto_retry_failed: Optional[bool] = None
    max_retries: Optional[int] = None
    chunk_size_kb: Optional[int] = None
    verify_checksum: Optional[bool] = None
    default_model_directory: Optional[str] = None
    default_media_directory: Optional[str] = None
