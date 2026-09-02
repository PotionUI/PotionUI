"""Compute-provisioning DTOs for request/response models."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProvisionProgressEntry(BaseModel):
    stage: str
    message: str
    percent: Optional[int] = None
    at: str


class ProvisionComputeRequest(BaseModel):
    """Request model for provisioning compute through a registered provider,
    into an EXISTING `native.remote` backend (`backend_id`). `values` holds
    the provider's own fields, keyed and typed as described by that
    provider's `POST providers/{id}/fields` - core validates them against
    those descriptors before calling the provisioner. `name` (the
    provisioner's `profile_name`) is optional and defaults to the target
    backend's own name."""
    provider_id: str
    backend_id: str
    name: Optional[str] = None
    values: Dict[str, Any] = Field(default_factory=dict)


class ProviderFieldsRequest(BaseModel):
    """Body for `POST providers/{id}/fields`. `values` is whatever the form
    has been filled in with so far (possibly partial, possibly empty) - a
    provisioner with dependent fields (`ComputeFieldDescriptorV1.depends_on`)
    uses it to resolve a dependent field's options."""
    values: Dict[str, Any] = Field(default_factory=dict)


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
    status_detail: Optional[str]
    status_checked_at: Optional[str]
    progress: List[ProvisionProgressEntry]
    created_at: Optional[str]
    updated_at: Optional[str]


class ProvisionedComputeListResponse(BaseModel):
    items: List[ProvisionedComputeResponse]
