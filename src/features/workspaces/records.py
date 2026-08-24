from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional
import json


@dataclass
class Workspace:
    """Model representing a saved workspace (tab layout configuration)."""
    id: str
    user_id: str
    name: str
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'name': self.name,
            'data': self.data,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def to_db_dict(self) -> dict:
        """Convert to dictionary for database insertion (with JSON serialized data)."""
        return {
            'user_id': self.user_id,
            'name': self.name,
            'data': json.dumps(self.data),
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }
