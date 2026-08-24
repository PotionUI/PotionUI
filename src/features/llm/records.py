from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any

from src.platform.database.rows import json_column

@dataclass
class LLMConfiguration:
    id: str
    name: str
    type: str
    enabled: bool
    base_url: str
    api_key: Optional[str]
    model: str
    system_message: str
    temperature: float = 0.7
    max_tokens: int = 2000
    timeout: int = 30
    supports_vision: bool = False
    disable_system_prompt: bool = False
    # One extra LLM call per conversation, extracting durable user facts from
    # the transcript into memory notes. Default ON.
    memory_reflection: bool = True
    provider_options: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # True when the stored api_key exists but cannot be decrypted with the
    # current key. The key itself is withheld (None) so it can never be used;
    # the flag lets the admin UI say "re-enter this" instead of showing an
    # empty field with no explanation.
    api_key_unreadable: bool = False

    @classmethod
    def from_row(cls, row) -> 'LLMConfiguration':
        """Create LLMConfiguration instance from database row"""
        provider_options = json_column(row['provider_options']) if 'provider_options' in row.keys() else None

        return cls(
            id=row['id'],
            name=row['name'],
            type=row['type'],
            enabled=bool(row['enabled']),
            base_url=row['base_url'],
            api_key=row['api_key'],
            model=row['model'],
            system_message=row['system_message'],
            temperature=float(row['temperature']),
            max_tokens=int(row['max_tokens']),
            timeout=int(row['timeout']),
            supports_vision=bool(row['supports_vision']) if 'supports_vision' in row.keys() else False,
            disable_system_prompt=bool(row['disable_system_prompt']) if 'disable_system_prompt' in row.keys() else False,
            memory_reflection=bool(row['memory_reflection']) if 'memory_reflection' in row.keys() else True,
            provider_options=provider_options,
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type,
            'enabled': self.enabled,
            'base_url': self.base_url,
            'api_key': self.api_key,
            'model': self.model,
            'system_message': self.system_message,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'timeout': self.timeout,
            'supports_vision': self.supports_vision,
            'disable_system_prompt': self.disable_system_prompt,
            'memory_reflection': self.memory_reflection,
            'provider_options': self.provider_options,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }