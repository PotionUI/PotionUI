from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class GenerationSource:
    """One `<field>__origin` link: `generation_id`'s `field_name` was seeded
    from `source_generation_id`'s output at `source_file_index`, rather than
    a bare upload."""
    generation_id: str
    field_name: str
    source_generation_id: str
    source_file_index: int
    id: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'GenerationSource':
        """Create GenerationSource instance from database row"""
        return cls(
            id=row['id'],
            generation_id=row['generation_id'],
            field_name=row['field_name'],
            source_generation_id=row['source_generation_id'],
            source_file_index=row['source_file_index'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'generation_id': self.generation_id,
            'field_name': self.field_name,
            'source_generation_id': self.source_generation_id,
            'source_file_index': self.source_file_index,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
