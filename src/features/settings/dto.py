from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from datetime import datetime


class SettingsSchema(BaseModel):
    """Legacy settings schema. Provider API keys are now managed via the plugin settings system."""
    models_dir: str
    device: str
    dtype: str
    nsfw: bool


class SettingResponse(BaseModel):
    """Response model for individual settings"""
    id: str
    key: str
    value: Any
    value_type: str
    description: Optional[str]
    type: str
    created_at: datetime
    updated_at: datetime


class UserSettingResponse(BaseModel):
    """Response model for user setting overrides"""
    id: str
    user_id: str
    setting_id: str
    setting_key: str
    value: Any
    created_at: datetime
    updated_at: datetime


class SettingUpdateRequest(BaseModel):
    """Request model for updating settings"""
    value: Any
    description: Optional[str] = None


class UserSettingUpdateRequest(BaseModel):
    """Request model for updating user settings"""
    value: Any


class SystemInfo(BaseModel):
    gpu_info: Dict[str, Any]
    memory_info: Dict[str, Any]
    disk_info: Dict[str, Any]
    models_count: int
    presets_count: int
