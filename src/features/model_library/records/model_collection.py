from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.platform.database.rows import row_get


@dataclass
class ModelCollection:
    """A named, user-owned virtual grouping of models."""
    id: str
    name: str
    user_id: str
    parent_id: Optional[str] = None  # Parent collection id for nesting (None = root)
    created_at: Optional[datetime] = None
    item_count: Optional[int] = None  # Number of models in the collection (populated by list queries)

    @classmethod
    def from_row(cls, row) -> 'ModelCollection':
        """Create ModelCollection instance from database row"""
        return cls(
            id=row['id'],
            name=row['name'],
            user_id=row['user_id'],
            parent_id=row_get(row, 'parent_id'),
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            item_count=row_get(row, 'item_count')
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'name': self.name,
            'user_id': self.user_id,
            'parent_id': self.parent_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'item_count': self.item_count,
        }
