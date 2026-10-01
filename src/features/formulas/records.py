from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.platform.database.rows import dt_column, dt_iso, json_column


@dataclass
class Formula:
    id: str
    owner_id: Optional[str]
    preset_id: str
    mode: str
    name: str
    variant: Optional[str] = None
    note: Optional[str] = None
    groups: List[Dict[str, Any]] = field(default_factory=list)
    values: Dict[str, Any] = field(default_factory=dict)
    signatures: Dict[str, Any] = field(default_factory=dict)
    preset_version: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "Formula":
        return cls(
            id=row["id"],
            owner_id=row["owner_id"],
            preset_id=row["preset_id"],
            mode=row["mode"],
            name=row["name"],
            variant=row["variant"],
            note=row["note"],
            groups=json_column(row["groups"], []),
            values=json_column(row["values_json"], {}),
            signatures=json_column(row["signatures"], {}),
            preset_version=row["preset_version"] or "",
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "preset_id": self.preset_id,
            "mode": self.mode,
            "variant": self.variant,
            "name": self.name,
            "note": self.note,
            "groups": self.groups,
            "values": self.values,
            "signatures": self.signatures,
            "preset_version": self.preset_version,
            "created_at": dt_iso(self.created_at),
            "updated_at": dt_iso(self.updated_at),
        }
