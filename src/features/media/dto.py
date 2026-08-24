"""
DTOs for media API endpoints.

Request/Response models for media management operations.
"""

from typing import Optional, List, Dict, Any
from pathlib import Path
from pydantic import BaseModel


# ============ Result DTOs ============

class MediaResult(BaseModel):
    """Result for media serving operations."""

    content: Optional[bytes] = None
    file_path: Optional[str] = None
    media_type: str
    headers: Dict[str, str] = {}
    use_streaming: bool = False

    class Config:
        arbitrary_types_allowed = True


class UploadResult(BaseModel):
    """Result from media upload operation."""

    path: str
    relative_path: str
    filename: str
    size: int
    url: str
    # Best-effort probed metadata. None when not determined -
    # duration_seconds/fps are always None for images.
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None


class MediaFileInfo(BaseModel):
    """Information about a media file."""

    id: str
    filename: str
    file_type: str
    mime_type: Optional[str] = None
    size: Optional[int] = None
    url: str
    thumbnail_small: Optional[str] = None
    thumbnail_medium: Optional[str] = None
    thumbnail_large: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None


class UploadInfoResult(BaseModel):
    """Best-effort metadata for an already-uploaded file, resolved on demand.

    Companion to `UploadResult` (returned at upload time): a saved form value
    that references an upload from a previous session has no metadata of its
    own to round-trip, so MediaSelect fetches this once and caches it
    client-side.
    """

    filename: str
    size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None


class MediaListResult(BaseModel):
    """Result from listing media files."""

    generation_id: str
    media_count: int
    media: List[MediaFileInfo]


class DeleteResult(BaseModel):
    """Result from delete media operation."""

    generation_id: str
    deleted_files: int
    failed_files: int


class UploadFileInfo(BaseModel):
    """One item in a user's upload library."""

    id: str
    filename: str
    original_filename: Optional[str] = None
    media_type: str  # 'image' | 'video' | 'audio'
    mime_type: Optional[str] = None
    url: str
    thumbnail_small: Optional[str] = None
    thumbnail_medium: Optional[str] = None
    thumbnail_large: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None
    size: Optional[int] = None
    created_at: Optional[str] = None


class UploadListResult(BaseModel):
    """Result from listing a user's uploads."""

    uploads: List[UploadFileInfo]
    total: int
    limit: int
    offset: int


class FileParamsResult(BaseModel):
    """Result from getting file parameters."""

    file_id: str
    generation_id: str
    file_path: str
    file_type: str
    mime_type: Optional[str] = None
    created_at: Optional[str] = None
    generation: Dict[str, Any]
    parameters: List[Dict[str, Any]]
