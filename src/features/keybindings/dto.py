from typing import Optional
from pydantic import BaseModel


class KeybindingResponse(BaseModel):
    action_id: str
    key: Optional[str]
    modifiers: str
    label: str
    category: str
    context: str
    description: Optional[str]
    enabled: bool
    is_custom: bool


class UpdateKeybindingRequest(BaseModel):
    key: Optional[str] = None
    modifiers: str = ''
