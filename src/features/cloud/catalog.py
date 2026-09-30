import asyncio
from typing import Any, Dict, Optional, Sequence

from src.features.cloud.backend import CloudBackend
from src.features.cloud.contracts import CLOUD_ENGINE, MODALITIES, TASK_KINDS, CloudProvider, spec_problems
from src.features.cloud.dto import admin_entry_dict, admin_state_dict
from src.features.cloud.errors import (
    CloudBackendInactive,
    CloudBackendNotFound,
    CloudCatalogFilterError,
    CloudEntryNotFound,
    CloudEntryUnavailable,
)
from src.features.cloud.records import CloudCatalogEntry
from src.features.cloud.repository import CloudCatalogRepository
from src.features.cloud.slug import cloud_model_slug
from src.features.models.records import ModelInfo
from src.platform.database.rows import now_iso
from src.platform.filesystem.model_types import CLOUD_MODEL_TYPE
from src.platform.observability.logger import logger


class CloudCatalog:
    def __init__(self, backend_registry, repository: CloudCatalogRepository, model_repository, backend_indexer):
        self.backend_registry = backend_registry
        self.repository = repository
        self.model_repository = model_repository
        self.backend_indexer = backend_indexer
        self._locks: Dict[str, asyncio.Lock] = {}

    def _lock(self, backend_id: str) -> asyncio.Lock:
        lock = self._locks.get(backend_id)
        if lock is None:
            lock = self._locks[backend_id] = asyncio.Lock()
        return lock

    def _config(self, backend_id: str):
        config = self.backend_registry.backend_config_store.get_backend(backend_id)
        if config is None or config.engine != CLOUD_ENGINE:
            raise CloudBackendNotFound(f"Cloud backend '{backend_id}' not found")
        return config

    def _active(self, backend_id: str) -> CloudBackend:
        self._config(backend_id)
        backend = self.backend_registry.get_backend(backend_id)
        if not isinstance(backend, CloudBackend):
            raise CloudBackendInactive(f"Cloud backend '{backend_id}' is not active. Enable it first.")
        return backend

    def _provider_class(self, config) -> Optional[type[CloudProvider]]:
        backend_class = self.backend_registry.get_registered_backend_types().get(config.effective_driver)
        return getattr(backend_class, "provider_class", None)

    def list_entries(
        self,
        backend_id: str,
        *,
        task: Optional[str] = None,
        output: Optional[str] = None,
        enabled: Optional[bool] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        config = self._config(backend_id)
        if task and task not in TASK_KINDS:
            raise CloudCatalogFilterError(f"Unknown task '{task}'. Expected one of: {', '.join(TASK_KINDS)}")
        if output and output not in MODALITIES:
            raise CloudCatalogFilterError(f"Unknown output '{output}'. Expected one of: {', '.join(MODALITIES)}")
        entries, total = self.repository.search(
            backend_id, task=task, output=output, enabled=enabled, search=search, limit=limit, offset=offset
        )
        provider_class = self._provider_class(config)
        return {
            "backend_id": backend_id,
            "driver": config.driver,
            "provider": {
                "key": provider_class.key if provider_class else None,
                "label": provider_class.label if provider_class else None,
                "data_notice": provider_class.data_notice if provider_class else "",
                "supports_cancel": bool(provider_class.supports_cancel) if provider_class else False,
            },
            "state": admin_state_dict(self.repository.get_state(backend_id)),
            "counts": self.repository.counts(backend_id),
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [admin_entry_dict(entry) for entry in entries],
        }

    async def refresh(self, backend_id: str) -> Dict[str, Any]:
        backend = self._active(backend_id)
        async with self._lock(backend_id):
            specs = await backend.provider.discover()
            if not specs:
                logger.warning(f"[CLOUD_CATALOG] {backend.name}: the provider returned no models; nothing was changed")
                return {
                    "backend_id": backend_id,
                    "empty": True,
                    "message": "The provider returned no models; nothing was changed.",
                    "refreshed_at": None,
                    "listed": 0,
                    "accepted": 0,
                    "created": 0,
                    "vanished": [],
                    "skipped": [],
                    "index": None,
                }
            now = now_iso()
            known = self.repository.provider_ids(backend_id)
            skipped: list[dict[str, Any]] = []
            claimed: Dict[str, str] = {}
            created = 0
            for spec in sorted(specs, key=lambda item: item.provider_model_id):
                problems = spec_problems(spec)
                slug = cloud_model_slug(backend.provider_class.key, spec.provider_model_id) if spec.provider_model_id else ""
                if not problems and slug in claimed:
                    problems.append(f"slug {slug!r} is already used by {claimed[slug]!r}")
                elif not problems and known.get(slug, spec.provider_model_id) != spec.provider_model_id:
                    problems.append(f"slug {slug!r} already belongs to {known[slug]!r}")
                if problems:
                    skipped.append({"provider_model_id": spec.provider_model_id, "problems": problems})
                    continue
                claimed[slug] = spec.provider_model_id
                if self.repository.upsert_discovered(backend_id, slug, spec, now):
                    created += 1
            vanished = self.repository.mark_missing(backend_id, claimed, now)
            self.repository.mark_suggested(backend_id, backend.provider.suggested_model_ids())
            self.repository.write_state(backend_id, now, len(specs), skipped)
            index = await self._sync(backend)
        logger.info(
            f"[CLOUD_CATALOG] {backend.name}: {len(specs)} listed, {created} new, "
            f"{len(vanished)} vanished, {len(skipped)} skipped"
        )
        return {
            "backend_id": backend_id,
            "empty": False,
            "message": None,
            "refreshed_at": now,
            "listed": len(specs),
            "accepted": len(claimed),
            "created": created,
            "vanished": vanished,
            "skipped": skipped,
            "index": index,
        }

    async def set_enabled(self, backend_id: str, slugs: Sequence[str], enabled: bool) -> Dict[str, Any]:
        backend = self._active(backend_id)
        ordered = list(dict.fromkeys(slugs))
        async with self._lock(backend_id):
            entries = {entry.slug: entry for entry in self.repository.get_many(backend_id, ordered)}
            unknown = [slug for slug in ordered if slug not in entries]
            if unknown:
                raise CloudEntryNotFound(unknown)
            if enabled:
                gone = [slug for slug in ordered if not entries[slug].available]
                if gone:
                    raise CloudEntryUnavailable(gone)
            changed = self.repository.set_enabled(backend_id, ordered, enabled, now_iso())
            index = await self._sync(backend)
            models = {
                entry.slug: entry.model_id
                for entry in self.repository.get_many(backend_id, ordered)
            }
        return {
            "backend_id": backend_id,
            "enabled": enabled,
            "changed": changed,
            "unchanged": [slug for slug in ordered if slug not in changed],
            "models": models,
            "index": index,
        }

    async def _sync(self, backend: CloudBackend) -> Dict[str, Any]:
        result = await self.backend_indexer.index_backend(backend)
        self._ensure_provider_rows(backend)
        return result.to_dict()

    def _ensure_provider_rows(self, backend: CloudBackend) -> None:
        for entry in self.repository.list_enabled(backend.backend_id):
            model = self.model_repository.get_by_identity(CLOUD_MODEL_TYPE, entry.slug, include_providers=False)
            if model is None:
                continue
            self.model_repository.upsert_provider(model.id, self._provider_info(backend, entry))

    @staticmethod
    def _provider_info(backend: CloudBackend, entry: CloudCatalogEntry) -> ModelInfo:
        return ModelInfo(
            provider=backend.driver,
            provider_model_id=entry.provider_model_id,
            name=entry.label,
            description=entry.spec.description,
            tags=[entry.vendor] if entry.vendor else [],
        )
