import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.platform.database.rows import row_get


def _parse_timestamp(value) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


@dataclass
class ProvisionedCompute:
    """One rented compute resource provisioned through a `ComputeProvisioner`,
    and the `native.remote` backend row core created for it (see
    `src.features.provisioning.operations.provision_compute`)."""
    id: str
    provider_id: str
    handle: str  # Opaque to core - echoed back to the provisioner unchanged. "" until provision() returns.
    profile_name: str
    status: str  # One of contracts.COMPUTE_STATES
    backend_id: Optional[str] = None  # Linked native.remote backend row, if any
    resource_ref: Optional[str] = None  # Provider's own display id (e.g. a pod id) - never used to address the provisioner
    gpu_type_id: Optional[str] = None
    region: Optional[str] = None
    created_by: Optional[str] = None
    status_detail: Optional[str] = None  # Provider-facing reason behind `status`, or the latest progress message
    status_checked_at: Optional[datetime] = None  # Last time `status` was reconciled against the provider
    progress: List[Dict[str, Any]] = field(default_factory=list)  # Bring-up timeline: {stage, message, percent, at}
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "ProvisionedCompute":
        raw_progress = row_get(row, "progress")
        return cls(
            id=row["id"],
            provider_id=row["provider_id"],
            handle=row["handle"],
            profile_name=row["profile_name"],
            status=row["status"],
            backend_id=row_get(row, "backend_id"),
            resource_ref=row_get(row, "resource_ref"),
            gpu_type_id=row_get(row, "gpu_type_id"),
            region=row_get(row, "region"),
            created_by=row_get(row, "created_by"),
            status_detail=row_get(row, "status_detail"),
            status_checked_at=_parse_timestamp(row_get(row, "status_checked_at")),
            progress=json.loads(raw_progress) if raw_progress else [],
            created_at=_parse_timestamp(row_get(row, "created_at")),
            updated_at=_parse_timestamp(row_get(row, "updated_at")),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider_id": self.provider_id,
            "handle": self.handle,
            "profile_name": self.profile_name,
            "status": self.status,
            "backend_id": self.backend_id,
            "resource_ref": self.resource_ref,
            "gpu_type_id": self.gpu_type_id,
            "region": self.region,
            "created_by": self.created_by,
            "status_detail": self.status_detail,
            "status_checked_at": self.status_checked_at.isoformat() if self.status_checked_at else None,
            "progress": list(self.progress),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
