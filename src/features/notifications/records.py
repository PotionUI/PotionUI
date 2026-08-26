"""
Notification model.

Persistent, per-user notification rows. System-wide broadcasts are fanned
out to one row per user at creation time (see NotificationRepository.create
callers in src.features.notifications.operations.notify).
"""
from enum import Enum
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class NotificationLevel(str, Enum):
    """Severity/level of a notification."""
    SUCCESS = "success"
    ERROR = "error"
    INFO = "info"
    WARNING = "warning"


class Notification(BaseModel):
    """A persisted notification for a single user."""
    id: str
    user_id: str
    category: str = "system"
    level: NotificationLevel
    title: str
    message: str = ""
    metadata: Optional[Dict[str, Any]] = None
    source: str = "core"
    type: str = ""
    read: bool = False
    created_at: datetime = Field(default_factory=datetime.now)

    class Config:
        use_enum_values = True
