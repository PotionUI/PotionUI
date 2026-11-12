from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from src.platform.database.rows import dt_column, row_get


@dataclass
class Kit:
    id: str
    owner_id: str
    name: str
    handle: str
    label: Optional[str] = None
    position: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "Kit":
        return cls(
            id=row["id"],
            owner_id=row["owner_id"],
            name=row["name"],
            handle=row["handle"],
            label=row_get(row, "label"),
            position=row_get(row, "position") or 0,
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
        )


@dataclass
class KitItem:
    id: str
    kit_id: str
    kind: str
    name: str
    position: int = 0
    media_ref: Optional[str] = None
    text: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "KitItem":
        return cls(
            id=row["id"],
            kit_id=row["kit_id"],
            kind=row["kind"],
            name=row["name"],
            position=row_get(row, "position") or 0,
            media_ref=row_get(row, "media_ref"),
            text=row_get(row, "text"),
            created_at=dt_column(row["created_at"]),
        )

    @property
    def is_text(self) -> bool:
        return self.kind == "text"


@dataclass
class KitSummary:
    kit: Kit
    counts: dict = field(default_factory=dict)
    thumbnails: List[dict] = field(default_factory=list)
