from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.platform.database.rows import dt_column, dt_iso, json_column

DEFAULT_GROUP = "Mine"


@dataclass
class UserFilter:
    id: str
    owner_id: str
    name: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    description: Optional[str] = None
    group_name: str = DEFAULT_GROUP
    intensity: int = 100
    schema_version: int = 1
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "UserFilter":
        return cls(
            id=row["id"],
            owner_id=row["owner_id"],
            name=row["name"],
            description=row["description"],
            group_name=row["group_name"] or DEFAULT_GROUP,
            intensity=row["intensity"],
            steps=json_column(row["steps_json"], []),
            schema_version=row["schema_version"],
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "group": self.group_name,
            "intensity": self.intensity,
            "steps": self.steps,
            "schema_version": self.schema_version,
            "created_at": dt_iso(self.created_at),
            "updated_at": dt_iso(self.updated_at),
        }
