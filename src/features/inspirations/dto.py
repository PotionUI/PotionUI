"""Inspirations request models and the record -> wire-dict projections.

The projections live here (not on the records) because they combine an
`Inspiration`/`InspirationComment`/`InspirationCollection` row with the
serving-URL convention (`/api/media/inspirations/...`) and the same
avatar-url formula `User.to_dict()` uses - neither of which the record
itself should know about.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.features.inspirations.records import Inspiration, InspirationComment, InspirationCollection


class PublishInspirationRequest(BaseModel):
    generation_id: str
    filenames: List[str] = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class CommentCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


class CreateInspirationCollectionRequest(BaseModel):
    name: str = Field(..., min_length=1)
    parent_id: Optional[str] = None


class UpdateInspirationCollectionRequest(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None


class InspirationCollectionItemRequest(BaseModel):
    inspiration_id: str


def _avatar_url(avatar_filename: Optional[str]) -> Optional[str]:
    return f"/api/users/avatars/{avatar_filename}" if avatar_filename else None


def inspiration_media_url(inspiration_id: str, filename: str) -> str:
    return f"/api/media/inspirations/{inspiration_id}/{filename}"


def inspiration_to_dto(insp: Inspiration) -> Dict[str, Any]:
    return {
        "id": insp.id,
        "title": insp.title,
        "description": insp.description,
        "author": {
            "id": insp.user_id,
            "username": insp.author_username,
            "avatar_url": _avatar_url(insp.author_avatar_filename),
        },
        "media": [
            {
                "url": inspiration_media_url(insp.id, entry["filename"]),
                "type": entry.get("type"),
                "width": entry.get("width"),
                "height": entry.get("height"),
            }
            for entry in insp.media
        ],
        "params_preview": insp.params_snapshot.get("preview", []),
        "technique": insp.technique,
        "created_at": insp.created_at.isoformat() if insp.created_at else None,
        "comment_count": insp.comment_count,
        "save_count": insp.save_count,
        "saved_by_me": insp.saved_by_me,
        "source_generation_id": insp.source_generation_id,
    }


def comment_to_dto(comment: InspirationComment) -> Dict[str, Any]:
    return {
        "id": comment.id,
        "user": {
            "id": comment.user_id,
            "username": comment.author_username,
            "avatar_url": _avatar_url(comment.author_avatar_filename),
        },
        "body": comment.body,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


def collection_to_dto(collection: InspirationCollection) -> Dict[str, Any]:
    return {
        "id": collection.id,
        "name": collection.name,
        "parent_id": collection.parent_id,
        "item_count": collection.item_count,
    }
