import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from src.features.edits.rules import clip_of
from src.platform.database.rows import dt_column, dt_iso, row_get


def _parse(raw):
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


@dataclass
class Edit:
    id: str
    user_id: str
    tool: str
    source_kind: str
    source_id: str
    source_name: Optional[str] = None
    name: Optional[str] = None
    state: Optional[Dict[str, Any]] = None
    thumbnail_upload_id: Optional[str] = None
    clip: Optional[Dict[str, Any]] = None
    revision: int = 1
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row, state: Optional[Dict[str, Any]] = None) -> "Edit":
        tool = row["tool"]
        clip = None
        if tool == "video":
            listed = row_get(row, "clip")
            clip = clip_of(state.get("clip") if state is not None else _parse(listed))
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            tool=row["tool"],
            source_kind=row["source_kind"],
            source_id=row["source_id"],
            source_name=row_get(row, "source_name"),
            name=row_get(row, "name"),
            state=state,
            thumbnail_upload_id=row_get(row, "thumbnail_upload_id"),
            clip=clip,
            revision=row["revision"],
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
        )

    def to_dict(self, with_state: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "tool": self.tool,
            "name": self.name,
            "source": {"kind": self.source_kind, "id": self.source_id, "name": self.source_name},
            "thumbnail_upload_id": self.thumbnail_upload_id,
            "revision": self.revision,
            "created_at": dt_iso(self.created_at),
            "updated_at": dt_iso(self.updated_at),
        }
        if self.tool == "video":
            data["clip"] = self.clip
        if with_state:
            data["state"] = self.state
        return data
