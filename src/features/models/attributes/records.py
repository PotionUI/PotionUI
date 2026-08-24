import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ModelAttributeDefinition:
    """One admin-managed attribute definition (a LoRA's `strength`, trigger
    words, ...). `model_types` empty means "applies to every model type"."""

    key: str
    label: str
    field_type: str  # 'slider' | 'number' | 'text' | 'select' | 'checkbox' | 'tags'
    id: Optional[str] = None
    model_types: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    default_value: Any = None
    description: Optional[str] = None
    per_user: bool = False
    admin_only: bool = False
    system: bool = False
    source: str = "user"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def applies_to(self, model_type: str) -> bool:
        return not self.model_types or model_type in self.model_types

    @classmethod
    def from_row(cls, row) -> "ModelAttributeDefinition":
        return cls(
            id=row["id"],
            key=row["key"],
            label=row["label"],
            field_type=row["field_type"],
            model_types=json.loads(row["model_types"]) if row["model_types"] else [],
            config=json.loads(row["config"]) if row["config"] else {},
            default_value=json.loads(row["default_value"]) if row["default_value"] is not None else None,
            description=row["description"],
            per_user=bool(row["per_user"]),
            admin_only=bool(row["admin_only"]),
            system=bool(row["system"]),
            source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "label": self.label,
            "field_type": self.field_type,
            "model_types": self.model_types,
            "config": self.config,
            "default_value": self.default_value,
            "description": self.description,
            "per_user": self.per_user,
            "admin_only": self.admin_only,
            "system": self.system,
            "source": self.source,
        }
