from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.platform.database.rows import dt_column, dt_iso


@dataclass
class UserModelMeta:
    """Per-user metadata overlay for a model (favorite flag, custom name)."""
    user_id: str
    model_id: str
    custom_name: Optional[str] = None
    is_favorite: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'UserModelMeta':
        """Create UserModelMeta instance from database row"""
        return cls(
            user_id=row['user_id'],
            model_id=row['model_id'],
            custom_name=row['custom_name'],
            is_favorite=bool(row['is_favorite']),
            created_at=dt_column(row['created_at']),
            updated_at=dt_column(row['updated_at']),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'user_id': self.user_id,
            'model_id': self.model_id,
            'custom_name': self.custom_name,
            'is_favorite': self.is_favorite,
            'created_at': dt_iso(self.created_at),
            'updated_at': dt_iso(self.updated_at),
        }
