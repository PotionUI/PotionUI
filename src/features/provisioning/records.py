from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.platform.database.rows import row_get


@dataclass
class ProvisionedCompute:
    """One rented compute resource provisioned through a `ComputeProvisioner`,
    and the `native.remote` backend row core created for it (see
    `src.features.provisioning.operations.provision_compute`)."""
    id: str
    provider_id: str
    handle: str  # Opaque to core - echoed back to the provisioner unchanged.
    profile_name: str
    status: str  # "running" | "stopped" | "missing" | "unreachable" | "unknown"
    backend_id: Optional[str] = None  # Linked native.remote backend row, if any
    resource_ref: Optional[str] = None  # Provider's own display id (e.g. a pod id) - never used to address the provisioner
    gpu_type_id: Optional[str] = None
    region: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "ProvisionedCompute":
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
            created_at=datetime.fromisoformat(row["created_at"]) if row_get(row, "created_at") else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row_get(row, "updated_at") else None,
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
