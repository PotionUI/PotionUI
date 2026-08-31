"""Tracking which RunPod resources this plugin manages, per provisioning
profile.

Own table via `db` from `src.plugin_api.storage`. One row per
(profile_name, resource_type): a profile has at most one managed Pod and at
most one managed network volume. `meta` is a small JSON blob for fields that
don't need their own column (currently just the worker's port, used to
reconstruct the RunPod proxy URL).

The worker token is not stored here - it goes through
`PluginRepository.set_plugin_setting(..., is_secret=True)` instead, the only
encryption-at-rest primitive `src.plugin_api` exposes to a plugin.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.plugin_api import db, generate_ulid

TABLE_NAME = "runpod_provider_resources"


@dataclass(frozen=True)
class ResourceRecord:
    id: str
    profile_name: str
    resource_type: str  # "pod" | "network_volume"
    runpod_id: str
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row) -> "ResourceRecord":
        return cls(
            id=row["id"],
            profile_name=row["profile_name"],
            resource_type=row["resource_type"],
            runpod_id=row["runpod_id"],
            meta=json.loads(row["meta"]) if row["meta"] else {},
        )


class RunPodResourceManager:
    def __init__(self) -> None:
        # Per-instance, not per-process: a shared flag would leak a test's
        # scratch-db "already ensured" state across instances.
        self._table_ensured = False

    def create_table(self) -> None:
        with db.get_cursor() as cursor:
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id TEXT PRIMARY KEY,
                    profile_name TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    runpod_id TEXT NOT NULL,
                    meta TEXT NOT NULL DEFAULT '{{}}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(profile_name, resource_type)
                )
            """)
        self._table_ensured = True

    def _ensure_table(self) -> None:
        if not self._table_ensured:
            self.create_table()

    def record(
        self,
        profile_name: str,
        resource_type: str,
        runpod_id: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> ResourceRecord:
        """Upsert: creating a resource for a (profile_name, resource_type)
        that already has one replaces it - the caller (`provisioning.py`)
        only calls this after confirming there is nothing to reuse, or after
        RunPod handed back a freshly created id."""
        self._ensure_table()
        payload = json.dumps(meta or {})
        with db.get_cursor() as cursor:
            cursor.execute(f"""
                INSERT INTO {TABLE_NAME} (id, profile_name, resource_type, runpod_id, meta, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(profile_name, resource_type)
                DO UPDATE SET runpod_id = excluded.runpod_id, meta = excluded.meta, updated_at = CURRENT_TIMESTAMP
            """, (generate_ulid(), profile_name, resource_type, runpod_id, payload))
        return self.get(profile_name, resource_type)

    def get(self, profile_name: str, resource_type: str) -> Optional[ResourceRecord]:
        self._ensure_table()
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {TABLE_NAME} WHERE profile_name = ? AND resource_type = ?",
                (profile_name, resource_type),
            )
            row = cursor.fetchone()
            return ResourceRecord.from_row(row) if row else None

    def list_for_profile(self, profile_name: str) -> List[ResourceRecord]:
        self._ensure_table()
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM {TABLE_NAME} WHERE profile_name = ? ORDER BY resource_type",
                (profile_name,),
            )
            return [ResourceRecord.from_row(row) for row in cursor.fetchall()]

    def delete(self, profile_name: str, resource_type: str) -> bool:
        self._ensure_table()
        with db.get_cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {TABLE_NAME} WHERE profile_name = ? AND resource_type = ?",
                (profile_name, resource_type),
            )
            return cursor.rowcount > 0
