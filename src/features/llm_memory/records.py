from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.platform.database.rows import dt_column, dt_iso


@dataclass
class LLMMemoryNote:
    """A persistent memory note stored by the LLM for a user."""
    id: Optional[str] = None
    user_id: str = ''
    key: str = ''
    content: str = ''
    scope: str = 'global'
    scope_ref: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'LLMMemoryNote':
        """Create LLMMemoryNote instance from database row."""
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            key=row['key'],
            content=row['content'],
            scope=row['scope'],
            scope_ref=row['scope_ref'],
            created_at=dt_column(row['created_at']),
            updated_at=dt_column(row['updated_at']),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'key': self.key,
            'content': self.content,
            'scope': self.scope,
            'scope_ref': self.scope_ref,
            'created_at': dt_iso(self.created_at),
            'updated_at': dt_iso(self.updated_at),
        }
