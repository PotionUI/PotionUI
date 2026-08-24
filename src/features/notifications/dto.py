"""
Notification DTOs for request/response models.
"""
from typing import Optional, Dict, Any
from pydantic import BaseModel

from src.features.notifications.records import NotificationLevel


class CreateNotificationRequest(BaseModel):
    """Request model for creating a notification from the frontend."""
    level: NotificationLevel
    title: str
    message: str = ""
    category: str = "frontend"
    transient: bool = False
    show_toast: bool = True
    metadata: Optional[Dict[str, Any]] = None
    type: str = ""


class UpdateNotificationPreferencesRequest(BaseModel):
    """Request model for updating a user's notification preferences (partial merge)."""
    types: Optional[Dict[str, bool]] = None
    sound: Optional[bool] = None
