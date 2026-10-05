import json
from typing import Iterable, Optional

from src.features.cloud.contracts import CloudModelSpec
from src.features.cloud.records import CloudCatalogEntry, CloudCatalogState
from src.features.cloud.spec_codec import SPEC_VERSION, spec_to_json
from src.platform.filesystem.model_types import CLOUD_MODEL_TYPE

_ENTRY_SELECT = (
    "SELECT c.*, m.id AS model_id FROM cloud_catalog c "
    "LEFT JOIN models m ON m.model_type = ? AND m.filename = c.slug"
)


def _placeholders(values: Iterable) -> str:
    return ",".join("?" for _ in values)


class CloudCatalogRepository:
    def upsert_discovered(self, backend_id: str, slug: str, spec: CloudModelSpec, now: str) -> bool:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM cloud_catalog WHERE backend_id = ? AND slug = ?",
                (backend_id, slug),
            )
            existed = cursor.fetchone() is not None
            cursor.execute(
                "INSERT INTO cloud_catalog "
                "(backend_id, slug, provider_model_id, label, vendor, tasks, outputs, spec, spec_version, "
                "deprecated_at, discovered_at, refreshed_at, missing_since) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT(backend_id, slug) DO UPDATE SET "
                "provider_model_id = excluded.provider_model_id, label = excluded.label, vendor = excluded.vendor, "
                "tasks = excluded.tasks, outputs = excluded.outputs, spec = excluded.spec, "
                "spec_version = excluded.spec_version, deprecated_at = excluded.deprecated_at, "
                "refreshed_at = excluded.refreshed_at, missing_since = NULL",
                (
                    backend_id,
                    slug,
                    spec.provider_model_id,
                    spec.label,
                    spec.vendor,
                    json.dumps(sorted(spec.tasks)),
                    json.dumps(sorted(spec.outputs)),
                    spec_to_json(spec),
                    SPEC_VERSION,
                    spec.deprecated_at,
                    now,
                    now,
                ),
            )
        return not existed

    def mark_missing(self, backend_id: str, seen_slugs: Iterable[str], now: str) -> list[str]:
        from src.platform.database.database import db

        seen = list(seen_slugs)
        clause = f" AND slug NOT IN ({_placeholders(seen)})" if seen else ""
        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT slug FROM cloud_catalog WHERE backend_id = ? AND missing_since IS NULL{clause}",
                (backend_id, *seen),
            )
            vanished = [row["slug"] for row in cursor.fetchall()]
            if vanished:
                cursor.execute(
                    f"UPDATE cloud_catalog SET missing_since = ? WHERE backend_id = ? AND slug IN ({_placeholders(vanished)})",
                    (now, backend_id, *vanished),
                )
        return vanished

    def provider_ids(self, backend_id: str) -> dict[str, str]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT slug, provider_model_id FROM cloud_catalog WHERE backend_id = ?",
                (backend_id,),
            )
            return {row["slug"]: row["provider_model_id"] for row in cursor.fetchall()}

    def entries_for_slug(self, slug: str, backend_ids: list[str]) -> list[CloudCatalogEntry]:
        if not backend_ids:
            return []
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"{_ENTRY_SELECT} WHERE c.slug = ? AND c.enabled = 1 AND c.missing_since IS NULL "
                f"AND c.backend_id IN ({_placeholders(backend_ids)})",
                (CLOUD_MODEL_TYPE, slug, *backend_ids),
            )
            return [CloudCatalogEntry.from_row(row) for row in cursor.fetchall()]

    def get_many(self, backend_id: str, slugs: list[str]) -> list[CloudCatalogEntry]:
        if not slugs:
            return []
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"{_ENTRY_SELECT} WHERE c.backend_id = ? AND c.slug IN ({_placeholders(slugs)})",
                (CLOUD_MODEL_TYPE, backend_id, *slugs),
            )
            return [CloudCatalogEntry.from_row(row) for row in cursor.fetchall()]

    def mark_suggested(self, backend_id: str, provider_model_ids: Iterable[str]) -> None:
        from src.platform.database.database import db

        wanted = list(dict.fromkeys(provider_model_ids))
        with db.get_cursor() as cursor:
            cursor.execute("UPDATE cloud_catalog SET suggested = 0 WHERE backend_id = ?", (backend_id,))
            if wanted:
                cursor.execute(
                    f"UPDATE cloud_catalog SET suggested = 1 WHERE backend_id = ? "
                    f"AND provider_model_id IN ({_placeholders(wanted)})",
                    (backend_id, *wanted),
                )

    def model_ids_for_tasks(self, backend_ids: list[str], tasks: list[str]) -> list[str]:
        if not backend_ids or not tasks:
            return []
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT m.id AS id FROM cloud_catalog c "
                "JOIN models m ON m.model_type = ? AND m.filename = c.slug "
                f"WHERE c.backend_id IN ({_placeholders(backend_ids)}) AND c.enabled = 1 AND c.missing_since IS NULL "
                f"AND EXISTS (SELECT 1 FROM json_each(c.tasks) WHERE json_each.value IN ({_placeholders(tasks)}))",
                (CLOUD_MODEL_TYPE, *backend_ids, *tasks),
            )
            return [row["id"] for row in cursor.fetchall()]

    def list_enabled(self, backend_id: str) -> list[CloudCatalogEntry]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"{_ENTRY_SELECT} WHERE c.backend_id = ? AND c.enabled = 1 AND c.missing_since IS NULL "
                "ORDER BY c.slug",
                (CLOUD_MODEL_TYPE, backend_id),
            )
            return [CloudCatalogEntry.from_row(row) for row in cursor.fetchall()]

    def search(
        self,
        backend_id: str,
        *,
        task: Optional[str] = None,
        output: Optional[str] = None,
        enabled: Optional[bool] = None,
        search: Optional[str] = None,
        suggested: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CloudCatalogEntry], int]:
        from src.platform.database.database import db

        where = ["c.backend_id = ?"]
        params: list = [backend_id]
        if task:
            where.append("EXISTS (SELECT 1 FROM json_each(c.tasks) WHERE json_each.value = ?)")
            params.append(task)
        if output:
            where.append("EXISTS (SELECT 1 FROM json_each(c.outputs) WHERE json_each.value = ?)")
            params.append(output)
        if enabled is not None:
            where.append("c.enabled = ?")
            params.append(1 if enabled else 0)
        if suggested is not None:
            where.append("c.suggested = ?")
            params.append(1 if suggested else 0)
        if search and search.strip():
            needle = "%" + search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append(
                "(c.label LIKE ? ESCAPE '\\' OR c.provider_model_id LIKE ? ESCAPE '\\' "
                "OR COALESCE(c.vendor, '') LIKE ? ESCAPE '\\')"
            )
            params.extend([needle, needle, needle])
        clause = " AND ".join(where)
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS n FROM cloud_catalog c WHERE {clause}", params)
            total = cursor.fetchone()["n"]
            cursor.execute(
                f"{_ENTRY_SELECT} WHERE {clause} ORDER BY c.enabled DESC, c.suggested DESC, LOWER(c.label), c.slug LIMIT ? OFFSET ?",
                (CLOUD_MODEL_TYPE, *params, limit, offset),
            )
            return [CloudCatalogEntry.from_row(row) for row in cursor.fetchall()], total

    def set_enabled(self, backend_id: str, slugs: list[str], enabled: bool, now: str) -> list[str]:
        if not slugs:
            return []
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT slug FROM cloud_catalog WHERE backend_id = ? AND enabled = ? AND slug IN ({_placeholders(slugs)})",
                (backend_id, 0 if enabled else 1, *slugs),
            )
            flipping = [row["slug"] for row in cursor.fetchall()]
            if flipping:
                cursor.execute(
                    f"UPDATE cloud_catalog SET enabled = ?, enabled_at = ? "
                    f"WHERE backend_id = ? AND slug IN ({_placeholders(flipping)})",
                    (1 if enabled else 0, now if enabled else None, backend_id, *flipping),
                )
        return flipping

    def counts(self, backend_id: str) -> dict[str, int]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(enabled), 0) AS enabled, "
                "COALESCE(SUM(CASE WHEN missing_since IS NOT NULL THEN 1 ELSE 0 END), 0) AS missing "
                "FROM cloud_catalog WHERE backend_id = ?",
                (backend_id,),
            )
            row = cursor.fetchone()
            return {"total": row["total"], "enabled": row["enabled"], "missing": row["missing"]}

    def write_state(self, backend_id: str, refreshed_at: str, listed: int, skipped: list[dict]) -> None:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO cloud_catalog_state (backend_id, refreshed_at, listed, skipped) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(backend_id) DO UPDATE SET refreshed_at = excluded.refreshed_at, "
                "listed = excluded.listed, skipped = excluded.skipped",
                (backend_id, refreshed_at, listed, json.dumps(skipped)),
            )

    def get_state(self, backend_id: str) -> Optional[CloudCatalogState]:
        from src.platform.database.database import db

        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM cloud_catalog_state WHERE backend_id = ?", (backend_id,))
            row = cursor.fetchone()
            return CloudCatalogState.from_row(row) if row else None
