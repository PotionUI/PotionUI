"""Compute-provisioning DTOs for request/response models."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProvisionComputeRequest(BaseModel):
    """Request model for provisioning compute through a registered provider.
    `values` holds the provider's own fields, keyed and typed as described by
    that provider's `GET providers/{id}/fields` - core validates them against
    those descriptors before calling the provisioner."""
    provider_id: str
    name: str = Field(..., min_length=1)
    values: Dict[str, Any] = Field(default_factory=dict)
    backend_name: Optional[str] = None


class ProviderResponse(BaseModel):
    provider_id: str
    label: str


class ProvisionedComputeResponse(BaseModel):
    id: str
    provider_id: str
    handle: str
    profile_name: str
    status: str
    backend_id: Optional[str]
    resource_ref: Optional[str]
    gpu_type_id: Optional[str]
    region: Optional[str]
    created_by: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


class ProvisionedComputeListResponse(BaseModel):
    items: List[ProvisionedComputeResponse]
