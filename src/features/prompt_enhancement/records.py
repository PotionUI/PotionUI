from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class EnhancementFeedback:
    """User verdict on an enhancement-proposed prompt."""
    id: Optional[str] = None
    user_id: str = ''
    session_id: str = ''
    message_id: str = ''
    prompt_text: str = ''
    verdict: str = ''  # 'approved' | 'rejected'
    model_id: Optional[str] = None
    reason: Optional[str] = None
    prompt_id: Optional[str] = None  # detached library Prompt created on approval
    mode: str = 'generation'  # chat mode the verdict was given in
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'EnhancementFeedback':
        """Create EnhancementFeedback instance from database row."""
        keys = row.keys() if hasattr(row, 'keys') else []
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            session_id=row['session_id'],
            message_id=row['message_id'],
            prompt_text=row['prompt_text'],
            verdict=row['verdict'],
            model_id=row['model_id'],
            reason=row['reason'],
            prompt_id=row['prompt_id'],
            mode=(row['mode'] if 'mode' in keys else None) or 'generation',
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'session_id': self.session_id,
            'message_id': self.message_id,
            'prompt_text': self.prompt_text,
            'verdict': self.verdict,
            'model_id': self.model_id,
            'reason': self.reason,
            'prompt_id': self.prompt_id,
            'mode': self.mode,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
