from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any
import json

@dataclass
class Backend:
    """A configured instance of an engine (see docs/backends.md).

    `driver` is the registered implementation this row uses - narrower than
    `engine`, which stays the preset-facing protocol name. An engine that only
    ever registered one implementation (e.g. `comfyui`) has `driver == engine`;
    `native` is the one engine with more than one driver (`native.local`, the
    always-present in-process implementation, and eventually `native.remote`).
    See migration 119.
    """
    id: str
    name: str
    engine: str
    driver: str
    enabled: bool
    is_default: bool
    config: Dict[str, Any]
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'Backend':
        """Create Backend instance from database row"""
        return cls(
            id=row['id'],
            name=row['name'],
            engine=row['engine'],
            driver=row['driver'],
            enabled=bool(row['enabled']),
            is_default=bool(row['is_default']),
            config=json.loads(row['config']) if row['config'] else {},
            description=row['description'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'name': self.name,
            'engine': self.engine,
            'driver': self.driver,
            'enabled': self.enabled,
            'is_default': self.is_default,
            'config': self.config,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def serialize_config(self) -> str:
        """Serialize config for database storage"""
        return json.dumps(self.config)
