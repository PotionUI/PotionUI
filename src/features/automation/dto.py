"""
Automation DTOs for request/response models.
"""
from typing import Any, Dict, Optional
from pydantic import BaseModel


class CreateAutomationRequest(BaseModel):
    """Request model for creating an automation."""
    name: str
    graph: Dict[str, Any]
    description: Optional[str] = None
    enabled: bool = False


class UpdateAutomationRequest(BaseModel):
    """Request model for partially updating an automation."""
    name: Optional[str] = None
    graph: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


class RunNowRequest(BaseModel):
    """Request model for manually triggering an automation run."""
    node_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


class ValidateGraphRequest(BaseModel):
    """Request model for validating a graph without persisting it."""
    graph: Dict[str, Any]


class ImportAutomationRequest(BaseModel):
    """Request model for importing an automation from an exported envelope."""
    document: Dict[str, Any]
    # Optional override for the imported automation's name (e.g. "Copy of X").
    name: Optional[str] = None


class InstantiateAutomationTemplateRequest(BaseModel):
    """Optional customization when cloning a catalog template."""

    name: Optional[str] = None
