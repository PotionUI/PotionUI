from pydantic import BaseModel


class McpTokenCreateRequest(BaseModel):
    name: str


class McpUserToggleRequest(BaseModel):
    enabled: bool
