import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.features.backends.backend_config import NATIVE_ENGINE, NATIVE_LOCAL_DRIVER
from src.features.backends.model_listing import CONFIDENCE_REPORTED, CONFIDENCE_VERIFIED
from src.features.models.availability_records import ModelAvailability
from src.features.models.availability_repository import model_availability_repo
from src.features.models.locations_repository import ModelLocationsRepository
from src.platform.filesystem.model_roots import ModelRootResolver, physical_legacy_ref

logger = logging.getLogger(__name__)


@dataclass
class ReconcileSummary:
    backend_ids: List[str] = field(default_factory=list)
    failed_backend_ids: List[str] = field(default_factory=list)
    created: int = 0
    matched: int = 0
    removed: int = 0


def _default_resolver() -> ModelRootResolver:
    from src.platform.filesystem.model_roots import RootProbe
    from src.platform.filesystem.model_roots_repository import ModelRootRepository

    return ModelRootResolver(ModelRootRepository(), RootProbe(), Path.cwd())


class NativeAvailabilityProjector:

    def __init__(
        self,
        resolver: Optional[ModelRootResolver] = None,
        locations_repository: Optional[ModelLocationsRepository] = None,
        availability_repository=None,
    ):
        self._resolver = resolver
        self.locations = locations_repository or ModelLocationsRepository()
        self.availability = availability_repository or model_availability_repo

    def _get_resolver(self) -> ModelRootResolver:
        if self._resolver is None:
            self._resolver = _default_resolver()
        return self._resolver

    async def reconcile(self, backend_registry) -> ReconcileSummary:
        summary = ReconcileSummary()
        if backend_registry is None:
            return summary

        try:
            local_backends = [
                b for b in backend_registry.get_backends_for_engine(NATIVE_ENGINE)
                if getattr(b.config, "driver", "") == NATIVE_LOCAL_DRIVER
            ]
        except Exception as exc:
            logger.warning(f"[NATIVE_AVAILABILITY] Could not list local native backends: {exc}")
            return summary

        if not local_backends:
            return summary

        try:
            projection = await asyncio.to_thread(self._project)
        except Exception as exc:
            logger.warning(f"[NATIVE_AVAILABILITY] Projection failed: {exc}")
            summary.failed_backend_ids = [b.backend_id for b in local_backends]
            return summary

        for backend in local_backends:
            try:
                created, matched, removed = await asyncio.to_thread(self._apply, backend.backend_id, projection)
            except Exception as exc:
                logger.warning(
                    f"[NATIVE_AVAILABILITY] Applying projection to backend '{backend.backend_id}' failed: {exc}"
                )
                summary.failed_backend_ids.append(backend.backend_id)
                continue
            summary.backend_ids.append(backend.backend_id)
            summary.created += created
            summary.matched += matched
            summary.removed += removed

        return summary

    def _project(self) -> Dict[str, Dict[str, Any]]:
        resolver = self._get_resolver()
        online_root_ids = list(resolver.online_root_ids())
        raw_path_by_root = {r.id: r.raw_path for r in resolver.roots() if r.id in online_root_ids}

        projection: Dict[str, Dict[str, Any]] = {}
        for model_id, location in self.locations.winners_by_model(online_root_ids).items():
            ref = physical_legacy_ref(raw_path_by_root[location['root_id']], location['subdir'], location['rel_path'])
            sha256 = location.get('sha256')
            projection[model_id] = {
                'ref': ref,
                'size': location.get('size'),
                'sha256': sha256,
                'confidence': CONFIDENCE_VERIFIED if sha256 else CONFIDENCE_REPORTED,
            }
        return projection

    def _apply(self, backend_id: str, projection: Dict[str, Dict[str, Any]]) -> Tuple[int, int, int]:
        created = 0
        matched = 0
        seen_model_ids = set()

        for model_id, entry in projection.items():
            existing = self.availability.get(model_id, backend_id)
            self.availability.upsert(ModelAvailability(
                id=None,
                model_id=model_id,
                backend_id=backend_id,
                ref=entry['ref'],
                size=entry['size'],
                confidence=entry['confidence'],
                digest=entry['sha256'],
            ))
            seen_model_ids.add(model_id)
            if existing is None:
                created += 1
            else:
                matched += 1

        removed = self.availability.delete_for_backend(backend_id, keep_model_ids=seen_model_ids)
        return created, matched, removed


NativeAvailabilityReconciler = NativeAvailabilityProjector

native_availability_projector = NativeAvailabilityProjector()
native_availability_reconciler = native_availability_projector
