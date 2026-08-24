"""
Database records for the media feature.

`Upload` is the ownership/metadata row for a file sent through the
MediaLoader form field's upload flow (migration 087). It is
deliberately separate from `src.features.generation.records.File`: an upload
has no generation to belong to and is written by `MediaManager.upload_media`
rather than the pipe/output path.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.platform.database.rows import row_get


@dataclass
class Upload:
    """One user-owned upload recorded in the `uploads` table (migration 087)."""

    user_id: str
    filename: str  # Unique on-disk name in storage/uploads/ (matches File.upload_media's uuid+ext)
    media_type: str  # 'image' | 'video' | 'audio'
    id: Optional[str] = None
    original_filename: Optional[str] = None  # As sent by the browser; display only, never used for path resolution
    mime_type: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None
    file_size: Optional[int] = None
    created_at: Optional[datetime] = None
    purpose: str = "user_upload"  # 'user_upload' | 'derived_artifact' (migration 120)
    # Paths relative to this row's own `uploads/<filename>` key, matching
    # `files.thumbnail_small/medium/large` (migration 128). NULL until a
    # thumbnail exists - synchronously for images, asynchronously for video.
    thumbnail_small: Optional[str] = None
    thumbnail_medium: Optional[str] = None
    thumbnail_large: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> 'Upload':
        """Create an Upload instance from a database row."""
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            filename=row['filename'],
            original_filename=row_get(row, 'original_filename'),
            media_type=row['media_type'],
            mime_type=row_get(row, 'mime_type'),
            width=row_get(row, 'width'),
            height=row_get(row, 'height'),
            duration_seconds=row_get(row, 'duration_seconds'),
            fps=row_get(row, 'fps'),
            file_size=row_get(row, 'file_size'),
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            purpose=row_get(row, 'purpose') or "user_upload",
            thumbnail_small=row_get(row, 'thumbnail_small'),
            thumbnail_medium=row_get(row, 'thumbnail_medium'),
            thumbnail_large=row_get(row, 'thumbnail_large'),
        )
