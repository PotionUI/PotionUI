from typing import Dict, Optional

from pydantic import BaseModel, StrictBool


class McpToolsPatchRequest(BaseModel):
    policy: Optional[str] = None
    overrides: Optional[Dict[str, Optional[StrictBool]]] = None


class McpKeyToolsPatchRequest(BaseModel):
    overrides: Optional[Dict[str, Optional[StrictBool]]] = None
    reset: Optional[StrictBool] = None


class McpTokenCreateRequest(BaseModel):
    name: str


class McpUserToggleRequest(BaseModel):
    enabled: bool
