"""Download records for the core download queue."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from enum import Enum
import json


class DownloadType(str, Enum):
    """Type of download"""
    MODEL = "model"
    MEDIA = "media"
    HF_REPO = "hf_repo"


class DownloadStatus(str, Enum):
    """Status of a download"""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Download:
    """Represents a download task in the system.

    An `hf_repo` download is a logical group: one parent row (this record,
    `type == HF_REPO`) whose per-file byte downloads are ordinary child rows
    carrying `group_id == parent.id`. The parent's progress/status are
    aggregates recomputed from its children (see DownloadRepository.refresh_group).
    """
    id: Optional[str] = None  # ULID
    type: DownloadType = DownloadType.MODEL
    url: str = ''
    destination_path: str = ''
    filename: str = ''
    status: DownloadStatus = DownloadStatus.PENDING
    progress: float = 0.0  # 0.0 to 1.0
    total_bytes: Optional[int] = None
    downloaded_bytes: int = 0
    speed_bytes_per_sec: Optional[float] = None
    error_message: Optional[str] = None
    provider_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    checksum_sha256: Optional[str] = None
    retry_count: int = 0
    group_id: Optional[str] = None  # parent download id for grouped (hf_repo) children
    repo_id: Optional[str] = None   # hf_repo parents: the Hugging Face repo id
    revision: Optional[str] = None  # hf_repo parents: the pinned revision, if any
    destination_backend_id: Optional[str] = None  # None = local disk; set = a native.remote worker's depot
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_by: Optional[str] = None  # user_id

    @classmethod
    def from_row(cls, row) -> 'Download':
        """Create Download instance from database row"""
        return cls(
            id=row['id'],
            type=DownloadType(row['type']),
            url=row['url'],
            destination_path=row['destination_path'],
            filename=row['filename'],
            status=DownloadStatus(row['status']),
            progress=float(row['progress']) if row['progress'] is not None else 0.0,
            total_bytes=row['total_bytes'],
            downloaded_bytes=row['downloaded_bytes'] or 0,
            speed_bytes_per_sec=float(row['speed_bytes_per_sec']) if row['speed_bytes_per_sec'] else None,
            error_message=row['error_message'],
            provider_id=row['provider_id'],
            tags=json.loads(row['tags']) if row['tags'] else [],
            checksum_sha256=row['checksum_sha256'],
            retry_count=row['retry_count'] or 0,
            group_id=row['group_id'],
            repo_id=row['repo_id'],
            revision=row['revision'],
            destination_backend_id=row['destination_backend_id'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
            completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
            created_by=row['created_by']
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'type': self.type.value,
            'url': self.url,
            'destination_path': self.destination_path,
            'filename': self.filename,
            'status': self.status.value,
            'progress': round(self.progress, 4),
            'total_bytes': self.total_bytes,
            'downloaded_bytes': self.downloaded_bytes,
            'speed_bytes_per_sec': round(self.speed_bytes_per_sec, 2) if self.speed_bytes_per_sec else None,
            'error_message': self.error_message,
            'provider_id': self.provider_id,
            'tags': self.tags,
            'checksum_sha256': self.checksum_sha256,
            'retry_count': self.retry_count,
            'group_id': self.group_id,
            'repo_id': self.repo_id,
            'revision': self.revision,
            'destination_backend_id': self.destination_backend_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'created_by': self.created_by
        }

    def get_eta_seconds(self) -> Optional[float]:
        """Calculate estimated time remaining in seconds"""
        if not self.speed_bytes_per_sec or self.speed_bytes_per_sec <= 0:
            return None
        if not self.total_bytes:
            return None
        remaining_bytes = self.total_bytes - self.downloaded_bytes
        if remaining_bytes <= 0:
            return 0
        return remaining_bytes / self.speed_bytes_per_sec

    def get_human_readable_size(self) -> str:
        """Get human readable file size"""
        if not self.total_bytes:
            return "Unknown"

        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if self.total_bytes < 1024:
                return f"{self.total_bytes:.2f} {unit}"
            self.total_bytes /= 1024
        return f"{self.total_bytes:.2f} PB"


@dataclass
class DownloadSettings:
    """Settings for the download service"""
    max_concurrent_downloads: int = 2
    auto_retry_failed: bool = True
    max_retries: int = 3
    chunk_size_kb: int = 1024  # 1MB chunks
    verify_checksum: bool = True
    default_model_directory: str = "models"
    default_media_directory: str = "storage/media"

    @classmethod
    def from_dict(cls, data: dict) -> 'DownloadSettings':
        """Create DownloadSettings from dictionary"""
        return cls(
            max_concurrent_downloads=data.get('max_concurrent_downloads', 2),
            auto_retry_failed=data.get('auto_retry_failed', True),
            max_retries=data.get('max_retries', 3),
            chunk_size_kb=data.get('chunk_size_kb', 1024),
            verify_checksum=data.get('verify_checksum', True),
            default_model_directory=data.get('default_model_directory', 'models'),
            default_media_directory=data.get('default_media_directory', 'storage/media')
        )

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            'max_concurrent_downloads': self.max_concurrent_downloads,
            'auto_retry_failed': self.auto_retry_failed,
            'max_retries': self.max_retries,
            'chunk_size_kb': self.chunk_size_kb,
            'verify_checksum': self.verify_checksum,
            'default_model_directory': self.default_model_directory,
            'default_media_directory': self.default_media_directory
        }
