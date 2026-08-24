from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.platform.database.rows import row_get


@dataclass
class Collection:
    """A named, user-owned virtual grouping of generations."""
    id: str
    name: str
    user_id: str
    scope: str  # 'history' | 'library' - which page's folder tree this belongs to (migration 137)
    parent_id: Optional[str] = None  # Parent collection id for nesting (None = root)
    created_at: Optional[datetime] = None
    item_count: Optional[int] = None  # Number of generations in the collection (populated by list queries)

    @classmethod
    def from_row(cls, row) -> 'Collection':
        """Create Collection instance from database row"""
        return cls(
            id=row['id'],
            name=row['name'],
            user_id=row['user_id'],
            scope=row['scope'],
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
            'scope': self.scope,
            'parent_id': self.parent_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'item_count': self.item_count,
        }
