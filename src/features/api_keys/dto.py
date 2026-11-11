from typing import List, Optional

from pydantic import BaseModel


class ApiKeyCreateRequest(BaseModel):
    name: Optional[str] = None
    scopes: Optional[List[str]] = None
    expires_in_days: Optional[int] = None


class ApiKeyUpdateRequest(BaseModel):
    name: Optional[str] = None
    scopes: Optional[List[str]] = None


class UserApiAccessRequest(BaseModel):
    keys_enabled: bool
    mcp_enabled: bool
