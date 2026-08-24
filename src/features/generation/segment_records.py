from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


@dataclass
class GenerationSegmentPhrasebook:
    """Records that a specific phrasebook value fed a prompt segment."""
    segment_id: str
    generation_id: str
    phrasebook_value_id: Optional[str] = None
    category_path: Optional[str] = None
    value: Optional[str] = None
    id: Optional[str] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'GenerationSegmentPhrasebook':
        """Create GenerationSegmentPhrasebook instance from database row"""
        return cls(
            id=row['id'],
            segment_id=row['segment_id'],
            generation_id=row['generation_id'],
            phrasebook_value_id=row['phrasebook_value_id'],
            category_path=row['category_path'],
            value=row['value'],
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'segment_id': self.segment_id,
            'generation_id': self.generation_id,
            'phrasebook_value_id': self.phrasebook_value_id,
            'category_path': self.category_path,
            'value': self.value,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


@dataclass
class GenerationSegment:
    """A resolved prompt segment (chip/timeline piece) belonging to a generation."""
    generation_id: str
    channel: str = 'positive'
    prompt_index: int = 0
    segment_index: int = 0
    segment_type: str = 'content'
    text: str = ''
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None
    is_disabled: bool = False
    id: Optional[str] = None
    created_at: Optional[datetime] = None
    phrasebooks: List[GenerationSegmentPhrasebook] = field(default_factory=list)

    @classmethod
    def from_row(cls, row) -> 'GenerationSegment':
        """Create GenerationSegment instance from database row"""
        return cls(
            id=row['id'],
            generation_id=row['generation_id'],
            channel=row['channel'],
            prompt_index=row['prompt_index'] or 0,
            segment_index=row['segment_index'] or 0,
            segment_type=row['segment_type'],
            text=row['text'],
            name=row['name'],
            color=row['color'],
            description=row['description'],
            is_disabled=bool(row['is_disabled']),
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'generation_id': self.generation_id,
            'channel': self.channel,
            'prompt_index': self.prompt_index,
            'segment_index': self.segment_index,
            'segment_type': self.segment_type,
            'text': self.text,
            'name': self.name,
            'color': self.color,
            'description': self.description,
            'is_disabled': self.is_disabled,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'phrasebooks': [a.to_dict() for a in self.phrasebooks]
        }
