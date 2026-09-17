import asyncio
import threading
from dataclasses import dataclass, field
from typing import Dict, List

from src.features.backends.backend_config import NATIVE_ENGINE, NATIVE_LOCAL_DRIVER
from src.platform.observability.logger import logger


def _default_indexer():
    from src.features.models.backend_indexer import backend_model_indexer

    return backend_model_indexer


@dataclass
class ReconcileSummary:
    backend_ids: List[str] = field(default_factory=list)
    failed_backend_ids: List[str] = field(default_factory=list)
    created: int = 0
    matched: int = 0
    removed: int = 0


class NativeAvailabilityReconciler:
    def __init__(self, indexer=None):
        self.indexer = indexer or _default_indexer()
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, backend_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(backend_id, threading.Lock())

    async def reconcile(self, backend_registry) -> ReconcileSummary:
        summary = ReconcileSummary()
        if backend_registry is None:
            return summary

        try:
            local_backends = [
                b for b in backend_registry.get_backends_for_engine(NATIVE_ENGINE)
                if getattr(b.config, "driver", "") == NATIVE_LOCAL_DRIVER
                and b.supports_model_listing()
            ]
        except Exception as exc:
            logger.warning(f"[NATIVE_AVAILABILITY] Could not list local native backends: {exc}")
            return summary

        for backend in local_backends:
            lock = self._lock_for(backend.backend_id)
            await asyncio.to_thread(lock.acquire)
            try:
                result = await self.indexer.index_backend(backend)
            except Exception as exc:
                logger.warning(
                    f"[NATIVE_AVAILABILITY] Reconcile failed for backend "
                    f"'{backend.backend_id}': {exc}"
                )
                summary.failed_backend_ids.append(backend.backend_id)
                continue
            finally:
                lock.release()
            summary.backend_ids.append(backend.backend_id)
            summary.created += result.created
            summary.matched += result.matched
            summary.removed += result.removed

        return summary


native_availability_reconciler = NativeAvailabilityReconciler()
