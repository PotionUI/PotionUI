"""Repository for `provisioned_compute` rows (migrations 004 + 007)."""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.platform.util.ids import generate_ulid
from src.features.provisioning.records import ProvisionedCompute

#: Oldest progress entries are dropped past this many - a provisioner that
#: reports every poll of a slow bring-up must not grow the row without bound.
PROGRESS_CAP = 50


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
        status_detail: Optional[str] = None,
    ) -> ProvisionedCompute:
        row_id = generate_ulid()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO provisioned_compute (
                    id, provider_id, handle, profile_name, status, backend_id,
                    resource_ref, gpu_type_id, region, created_by, status_detail, progress
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')
                """,
                (
                    row_id, provider_id, handle, profile_name, status, backend_id,
                    resource_ref, gpu_type_id, region, created_by, status_detail,
                ),
            )
        return self.get_by_id(row_id)

    def get_by_id(self, row_id: str) -> Optional[ProvisionedCompute]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM provisioned_compute WHERE id = ?", (row_id,))
            row = cursor.fetchone()
            return ProvisionedCompute.from_row(row) if row else None

    def get_by_backend_id(self, backend_id: str) -> Optional[ProvisionedCompute]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM provisioned_compute WHERE backend_id = ?", (backend_id,))
            row = cursor.fetchone()
            return ProvisionedCompute.from_row(row) if row else None

    def list_all(self) -> List[ProvisionedCompute]:
        # `id DESC` as a tiebreaker, not just `created_at`: SQLite's
        # CURRENT_TIMESTAMP has one-second resolution, and a ULID sorts
        # lexicographically by its own embedded creation time, so two rows
        # created within the same second still land newest-first.
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM provisioned_compute ORDER BY created_at DESC, id DESC")
            return [ProvisionedCompute.from_row(row) for row in cursor.fetchall()]

    def update_status(
        self,
        row_id: str,
        status: str,
        detail: Optional[str] = None,
        checked_at: Optional[datetime] = None,
    ) -> bool:
        """`detail` always overwrites (None clears it - a state with no reason
        must not keep the previous state's). `checked_at` is only written when
        given: the bring-up job sets a status without having asked the
        provider, the monitor sets both."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if checked_at is None:
                cursor.execute(
                    "UPDATE provisioned_compute SET status = ?, status_detail = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (status, detail, row_id),
                )
            else:
                cursor.execute(
                    "UPDATE provisioned_compute SET status = ?, status_detail = ?, status_checked_at = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (status, detail, checked_at.isoformat(), row_id),
                )
            return cursor.rowcount > 0

    def update_handle(self, row_id: str, handle: str, resource_ref: Optional[str] = None) -> bool:
        """Written once, when the bring-up job's `provision()` returns and the
        provisioner's own identifier for the resource is finally known."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE provisioned_compute SET handle = ?, resource_ref = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (handle, resource_ref, row_id),
            )
            return cursor.rowcount > 0

    def append_progress(self, row_id: str, entry: Dict[str, Any]) -> bool:
        """Append one bring-up step (keeping the newest `PROGRESS_CAP`) and
        mirror its message into `status_detail`, so the row's one-line detail
        is always the latest thing that happened."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT progress FROM provisioned_compute WHERE id = ?", (row_id,))
            row = cursor.fetchone()
            if row is None:
                return False
            entries = json.loads(row["progress"]) if row["progress"] else []
            entries.append(entry)
            entries = entries[-PROGRESS_CAP:]
            cursor.execute(
                "UPDATE provisioned_compute SET progress = ?, status_detail = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (json.dumps(entries), entry.get("message"), row_id),
            )
            return cursor.rowcount > 0

    def clear_backend_link(self, row_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE provisioned_compute SET backend_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row_id,),
            )
            return cursor.rowcount > 0

    def delete(self, row_id: str) -> bool:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM provisioned_compute WHERE id = ?", (row_id,))
            return cursor.rowcount > 0
