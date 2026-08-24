"""Row records for the media index tables (migration 098)."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MediaIndexQueueItem:
    id: str
    file_id: str
    pass_type: str
    status: str
    attempts: int
    last_error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    file_path: Optional[str] = None
    file_type: Optional[str] = None
    thumbnail_path: Optional[str] = None
    user_id: Optional[str] = None
    generation_id: Optional[str] = None
    prompt_text: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "MediaIndexQueueItem":
        keys = row.keys()

        def get(name):
            return row[name] if name in keys else None

        return cls(
            id=row["id"],
            file_id=row["file_id"],
            pass_type=row["pass_type"],
            status=row["status"],
            attempts=row["attempts"],
            last_error=get("last_error"),
            created_at=get("created_at"),
            updated_at=get("updated_at"),
            file_path=get("file_path"),
            file_type=get("file_type"),
            thumbnail_path=get("thumbnail_path"),
            user_id=get("file_user_id"),
            generation_id=get("generation_id"),
            prompt_text=get("prompt_text"),
        )
