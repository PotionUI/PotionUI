"""
Plugin DTOs for request/response models.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


# ========== Request DTOs ==========

class PluginSettingsUpdateRequest(BaseModel):
    """Request model for updating plugin settings."""
    settings: Dict[str, Any]


# ========== Response DTOs ==========

class PluginResponse(BaseModel):
    """Response model for plugin basic information."""
    id: str
    name: str
    version: str
    type: str  # 'frontend-only', 'backend-only', 'full-stack'
    enabled: bool
    manifest_path: str
    description: Optional[str] = None
    author: Optional[str] = None
    installed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    state: Optional[str] = None  # Runtime state from registry
    error: Optional[str] = None  # Error message if in error state
    category: str = "other"
    tags: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    source: str = "local"  # "marketplace" | "local"
    homepage: Optional[str] = None
    repository: Optional[str] = None
    hook_count: int = 0
    settings_count: int = 0


class PluginHookResponse(BaseModel):
    """Response model for plugin hook."""
    id: int
    plugin_id: str
    hook_name: str
    hook_type: str  # 'backend' or 'frontend'
    handler_path: Optional[str] = None
    component_path: Optional[str] = None
    position: Optional[str] = None
    sort_order: int = 0
    plugin_name: Optional[str] = None  # Enriched field
    plugin_version: Optional[str] = None  # Enriched field


class PluginSettingResponse(BaseModel):
    """Response model for plugin setting."""
    id: int
    plugin_id: str
    setting_key: str
    setting_value: Optional[str] = None  # Masked if is_secret
    user_id: Optional[str] = None
    is_secret: bool = False


class PluginDetailResponse(PluginResponse):
    """Response model for detailed plugin information."""
    hooks: List[PluginHookResponse] = Field(default_factory=list)
    settings_schema: List[Dict[str, Any]] = Field(default_factory=list)
    settings_values: Dict[str, Any] = Field(default_factory=dict)


class PluginPageResponse(BaseModel):
    """Response model for plugin page."""
    plugin_id: str
    route: str
    component_path: str
    label: str
    icon_svg: Optional[str] = None
    sidebar_order: int = 100
    show_in_sidebar: bool = True
    require_role: Optional[str] = None


class PluginScanResult(BaseModel):
    """Result of plugin scan operation."""
    new_plugins: List[PluginResponse] = Field(default_factory=list)
    updated_plugins: List[PluginResponse] = Field(default_factory=list)
    total_discovered: int = 0
