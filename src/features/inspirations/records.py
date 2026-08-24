"""Inspirations domain records - one dataclass per table (migration 136)."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.platform.database.rows import row_get as _safe_get


@dataclass
class Inspiration:
    """A published snapshot of a generation's output files.

    `media` and `params_snapshot` are the snapshot itself - copied files and
    an embedded params projection, both independent of the source generation
    once written. `source_generation_id` is provenance only; nothing here has
    a foreign key back to `generations`.

    `comment_count`/`save_count`/`saved_by_me`/`author_username`/
    `author_avatar_filename` are not columns - populated by the repository's
    feed/detail queries (joins + subqueries), zero-valued otherwise.
    """
    id: str
    user_id: str
    title: str
    media: List[Dict[str, Any]]
    params_snapshot: Dict[str, Any]
    description: Optional[str] = None
    preset_id: Optional[str] = None
    preset_name: Optional[str] = None
    # Derived at publish time (src.features.inspirations.technique.derive_technique).
    # NULL for rows published before this column existed - unknown, not a fake default.
    technique: Optional[str] = None
    source_generation_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    comment_count: int = 0
    save_count: int = 0
    saved_by_me: bool = False
    author_username: Optional[str] = None
    author_avatar_filename: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "Inspiration":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            description=_safe_get(row, "description"),
            media=json.loads(row["media"]) if row["media"] else [],
            params_snapshot=json.loads(row["params_snapshot"]) if row["params_snapshot"] else {},
            preset_id=_safe_get(row, "preset_id"),
            preset_name=_safe_get(row, "preset_name"),
            technique=_safe_get(row, "technique"),
            source_generation_id=_safe_get(row, "source_generation_id"),
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            comment_count=_safe_get(row, "comment_count", 0) or 0,
            save_count=_safe_get(row, "save_count", 0) or 0,
            saved_by_me=bool(_safe_get(row, "saved_by_me", 0)),
            author_username=_safe_get(row, "author_username"),
            author_avatar_filename=_safe_get(row, "author_avatar_filename"),
        )


@dataclass
class InspirationComment:
    id: str
    inspiration_id: str
    user_id: str
    body: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    author_username: Optional[str] = None
    author_avatar_filename: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "InspirationComment":
        return cls(
            id=row["id"],
            inspiration_id=row["inspiration_id"],
            user_id=row["user_id"],
            body=row["body"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            author_username=_safe_get(row, "author_username"),
            author_avatar_filename=_safe_get(row, "author_avatar_filename"),
        )


@dataclass
class InspirationCollection:
    id: str
    user_id: str
    name: str
    parent_id: Optional[str] = None
    created_at: Optional[datetime] = None
    item_count: int = 0

    @classmethod
    def from_row(cls, row) -> "InspirationCollection":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            parent_id=_safe_get(row, "parent_id"),
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            item_count=_safe_get(row, "item_count", 0) or 0,
        )
