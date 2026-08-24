from dataclasses import dataclass
from typing import Optional


@dataclass
class KeybindingDefault:
    id: str
    key: str
    modifiers: str
    label: str
    category: str
    context: str
    description: Optional[str] = None
    enabled: bool = True
    source: str = 'system'
    sort_order: int = 0

    @classmethod
    def from_row(cls, row) -> 'KeybindingDefault':
        return cls(
            id=row['id'],
            key=row['key'],
            modifiers=row['modifiers'] or '',
            label=row['label'],
            category=row['category'] or 'general',
            context=row['context'] or 'global',
            description=row['description'],
            enabled=bool(row['enabled']),
            source=row['source'] or 'system',
            sort_order=row['sort_order'] or 0
        )


@dataclass
class UserKeybinding:
    id: int
    user_id: str
    action_id: str
    key: Optional[str] = None
    modifiers: str = ''
    enabled: bool = True

    @classmethod
    def from_row(cls, row) -> 'UserKeybinding':
        return cls(
            id=row['id'],
            user_id=row['user_id'],
            action_id=row['action_id'],
            key=row['key'],
            modifiers=row['modifiers'] or '',
            enabled=bool(row['enabled'])
        )
