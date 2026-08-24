"""
Provider Data Transfer Objects (DTOs) for API requests and responses.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class ProviderSettingsUpdate(BaseModel):
    """Request model for updating provider settings."""
    settings: Dict[str, Any]


class ProviderTestResult(BaseModel):
    """Response model for provider connection test."""
    success: bool
    message: str


class ProviderInfo(BaseModel):
    """Response model for provider information."""
    id: str
    name: str
    description: str
    website: str
    capabilities: List[str]
    version: str
    initialized: bool
    icon: Optional[str] = None
