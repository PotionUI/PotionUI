from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
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
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
