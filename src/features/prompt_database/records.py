"""Normalized prompt aggregate model."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.features.segments.dto import RichSegment


@dataclass
class Prompt:
    user_id: str
    segments: List[RichSegment]
    id: Optional[str] = None
    name: Optional[str] = None
    flattened_text: str = ""
    usage_hint: Optional[str] = None
    source_group_id: Optional[str] = None
    source_provider: Optional[str] = None
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    model_id: Optional[str] = None
    model_name: Optional[str] = None
    base_model: Optional[str] = None
    cfg_scale: Optional[float] = None
    steps: Optional[int] = None
    sampler: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    heart_count: int = 0
    like_count: int = 0
    laugh_count: int = 0
    cry_count: int = 0
    comment_count: int = 0
    tags: List[str] = field(default_factory=list)
    nsfw: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedded: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        preview = " ".join((self.flattened_text or "").split())
        if not preview:
            return "Untitled prompt"
        return preview if len(preview) <= 72 else f"{preview[:69].rstrip()}…"

    # Search/enhancement call sites read the flattened positive text as `.prompt`.
    # This read-only convenience does not reintroduce paired prompt semantics or
    # provenance in editor state.
    @property
    def prompt(self) -> str:
        return self.flattened_text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "display_name": self.display_name,
            "flattened_text": self.flattened_text,
            "usage_hint": self.usage_hint,
            "segments": [segment.model_dump() for segment in self.segments],
            "source_group_id": self.source_group_id,
            "source_provider": self.source_provider,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "model_id": self.model_id,
            "model_name": self.model_name,
            "base_model": self.base_model,
            "cfg_scale": self.cfg_scale,
            "steps": self.steps,
            "sampler": self.sampler,
            "width": self.width,
            "height": self.height,
            "heart_count": self.heart_count,
            "like_count": self.like_count,
            "laugh_count": self.laugh_count,
            "cry_count": self.cry_count,
            "comment_count": self.comment_count,
            "tags": self.tags,
            "nsfw": self.nsfw,
            "metadata": self.metadata,
            "embedded": self.embedded,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

