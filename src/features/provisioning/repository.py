"""Repository for `provisioned_compute` rows (migration 004)."""

from typing import List, Optional

from src.platform.database import db
from src.platform.util.ids import generate_ulid
from src.features.provisioning.records import ProvisionedCompute


class ProvisionedComputeRepository:
    def create(
        self,
        *,
        provider_id: str,
        handle: str,
        profile_name: str,
        status: str,
        backend_id: Optional[str] = None,
        resource_ref: Optional[str] = None,
        gpu_type_id: Optional[str] = None,
        region: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> ProvisionedCompute:
        row_id = generate_ulid()
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO provisioned_compute (
                    id, provider_id, handle, profile_name, status, backend_id,
                    resource_ref, gpu_type_id, region, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id, provider_id, handle, profile_name, status, backend_id,
                    resource_ref, gpu_type_id, region, created_by,
                ),
            )
        return self.get_by_id(row_id)

    def get_by_id(self, row_id: str) -> Optional[ProvisionedCompute]:
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM provisioned_compute WHERE id = ?", (row_id,))
            row = cursor.fetchone()
            return ProvisionedCompute.from_row(row) if row else None

    def list_all(self) -> List[ProvisionedCompute]:
        # `id DESC` as a tiebreaker, not just `created_at`: SQLite's
        # CURRENT_TIMESTAMP has one-second resolution, and a ULID sorts
        # lexicographically by its own embedded creation time, so two rows
        # created within the same second still land newest-first.
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM provisioned_compute ORDER BY created_at DESC, id DESC")
            return [ProvisionedCompute.from_row(row) for row in cursor.fetchall()]

    def update_status(self, row_id: str, status: str) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE provisioned_compute SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, row_id),
            )
            return cursor.rowcount > 0

    def clear_backend_link(self, row_id: str) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE provisioned_compute SET backend_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row_id,),
            )
            return cursor.rowcount > 0

    def delete(self, row_id: str) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM provisioned_compute WHERE id = ?", (row_id,))
            return cursor.rowcount > 0
