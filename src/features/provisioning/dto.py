"""Compute-provisioning DTOs for request/response models."""

from typing import List, Optional
from pydantic import BaseModel, Field


class ProvisionComputeRequest(BaseModel):
    """Request model for provisioning compute through a registered provider."""
    provider_id: str
    profile_name: str = Field(..., min_length=1)
    gpu_type_id: Optional[str] = None
    region: Optional[str] = None
    image_ref: Optional[str] = None
    volume_size_gb: Optional[int] = None
    worker_port: int = 8100
    container_disk_gb: int = 20
    backend_name: Optional[str] = None


class GpuTypeResponse(BaseModel):
    id: str
    memory_gb: Optional[int]


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
