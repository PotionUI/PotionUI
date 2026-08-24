from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

from src.platform.database.rows import json_column


@dataclass
class ChatMessage:
    """Represents a single message in a chat session"""
    id: str
    session_id: str
    role: str  # 'user', 'assistant', 'system'
    content: str
    parsed_content: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> 'ChatMessage':
        """Create ChatMessage instance from database row"""
        parsed_content = json_column(row['parsed_content']) if 'parsed_content' in row.keys() else None
        metadata = json_column(row['metadata']) if 'metadata' in row.keys() else None

        return cls(
            id=row['id'],
            session_id=row['session_id'],
            role=row['role'],
            content=row['content'],
            parsed_content=parsed_content,
            metadata=metadata,
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'session_id': self.session_id,
            'role': self.role,
            'content': self.content,
            'parsed_content': self.parsed_content,
            'metadata': self.metadata,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


@dataclass
class ChatSession:
    """Represents a chat session with conversation history"""
    id: str
    user_id: str
    mode: str = 'generation'  # chat mode id (immutable for the session's lifetime)
    name: Optional[str] = None
    status: str = 'active'  # 'active', 'accepted', 'rejected'
    llm_config_id: Optional[str] = None
    original_text: Optional[str] = None
    title_generated: bool = False
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    messages: List[ChatMessage] = field(default_factory=list)

    @classmethod
    def from_row(cls, row, messages: Optional[List[ChatMessage]] = None) -> 'ChatSession':
        """Create ChatSession instance from database row"""
        metadata = json_column(row['metadata']) if 'metadata' in row.keys() else None

        return cls(
            id=row['id'],
            user_id=row['user_id'],
            mode=row['mode'],
            name=row['name'],
            status=row['status'],
            llm_config_id=row['llm_config_id'],
            original_text=row['original_text'],
            title_generated=bool(row['title_generated']) if 'title_generated' in row.keys() else False,
            metadata=metadata,
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
            closed_at=datetime.fromisoformat(row['closed_at']) if row['closed_at'] else None,
            messages=messages or []
        )

    def to_dict(self, include_messages: bool = True) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        result = {
            'id': self.id,
            'user_id': self.user_id,
            'mode': self.mode,
            'name': self.name,
            'status': self.status,
            'llm_config_id': self.llm_config_id,
            'original_text': self.original_text,
            'title_generated': self.title_generated,
            'metadata': self.metadata,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'closed_at': self.closed_at.isoformat() if self.closed_at else None
        }
        if include_messages:
            result['messages'] = [msg.to_dict() for msg in self.messages]
        return result

    def get_latest_suggestion(self) -> Optional[str]:
        """Extract the latest AI suggestion from messages"""
        for message in reversed(self.messages):
            if message.role == 'assistant' and message.parsed_content:
                return message.parsed_content.get('modifiedPrompt')
        return None

    def get_conversation_history(self) -> List[Dict[str, str]]:
        """Get conversation history in format suitable for LLM API"""
        return [
            {'role': msg.role, 'content': msg.content}
            for msg in self.messages
        ]
