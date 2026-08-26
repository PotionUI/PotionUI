"""
DTOs for the library API.

A library item is an upload row plus the curation attached to it (tags,
collections). It carries no generation fields even when it started life as a
copy of one - see `src.features.library.operations.copy_generation_file`.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class LibraryItem(BaseModel):
    """One resource in a user's library."""

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
    tags: List[Dict[str, Any]] = []


class LibraryListResult(BaseModel):
    """One page of a user's library."""

    items: List[LibraryItem]
    total: int
    limit: int
    offset: int


class LibraryFacets(BaseModel):
    """Counts a library page shows alongside its filters."""

    media_types: Dict[str, int] = {}


class CopyFromGenerationRequest(BaseModel):
    """Request model for copying a generated file into the library."""

    file_id: str


class SetLibraryTagsRequest(BaseModel):
    """Request model for replacing a library item's tags."""

    tag_ids: List[str]
