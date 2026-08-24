"""
Reconcile what a backend says it can load into `models` + `model_availability`.

Indexing is per backend because "which models exist" is a fact about a backend, not about
the application. A backend answers with `BackendModel` entries; this module resolves each
one to a logical model by `(model_type, filename)`, creating the row when it is new, and
records an availability row carrying that backend's engine-native `ref`.

Two backends holding the same filename are the same model. That is the whole merge rule:
ComfyUI cannot report hashes, so a hash can never be required. Size is compared, never
keyed on - see docs/models.md.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from src.features.backends.model_listing import (
    BackendModel,
    CONFIDENCE_CONFLICT,
    ModelListingNotSupported,
)
from src.platform.observability.logger import logger
from src.features.models.availability_records import ModelAvailability
from src.features.models.availability_repository import (
    model_availability_repo,
)


@dataclass
class SizeConflict:
    """Same identity, different byte count on different backends.

    The likely cause is a quantised copy that kept its filename. Merging them would mean
    generating with different weights depending on which backend won selection, so the
    conflict is surfaced rather than resolved.
    """

    model_type: str
    filename: str
    known_size: int
    reported_size: int
    backend_id: str


@dataclass
class DigestConflict:
    """Same identity, same-ish size, but this backend's own bytes hash differently
    from the model's canonical `models.sha256`.

    Unlike `SizeConflict`, this is not merely reported: `index_backend` marks the
    offending `model_availability` row `confidence = CONFIDENCE_CONFLICT`, which
    `ModelAvailabilityRepository.backends_holding`/`backend_ids_by_model` then exclude
    from routing. A partially-synced mirror or an interrupted resync produces exactly
    this - a file at the right path, right name, sometimes even right size, with
    different content - and generating from it would succeed silently on the wrong
    weights. See migration 110_model_availability_digest.py.
    """

    model_type: str
    filename: str
    known_digest: str
    reported_digest: str
    backend_id: str


@dataclass
class IndexResult:
    backend_id: str
    listed: int = 0
    created: int = 0
    matched: int = 0
    removed: int = 0
    size_conflicts: List[SizeConflict] = field(default_factory=list)
    digest_conflicts: List[DigestConflict] = field(default_factory=list)
    ambiguous: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "backend_id": self.backend_id,
            "listed": self.listed,
            "created": self.created,
            "matched": self.matched,
            "removed": self.removed,
            "size_conflicts": [c.__dict__ for c in self.size_conflicts],
            "digest_conflicts": [c.__dict__ for c in self.digest_conflicts],
            "ambiguous": self.ambiguous,
        }


class BackendModelIndexer:
    """Turns `backend.list_models()` into rows. One backend at a time."""

    def __init__(self, model_repository=None, availability_repository=None):
        if model_repository is None:
            from src.features.models.repository import model_repo
            model_repository = model_repo
        self.models = model_repository
        self.availability = availability_repository or model_availability_repo

    async def index_backend(self, backend) -> IndexResult:
        """Index one backend. Raises ModelListingNotSupported if it cannot enumerate."""
        result = IndexResult(backend_id=backend.backend_id)

        if not backend.supports_model_listing():
            raise ModelListingNotSupported(
                f"Backend '{backend.name}' (engine={backend.engine}) cannot enumerate its models"
            )

        entries = await backend.list_models()
        result.listed = len(entries)
        logger.info(
            f"[BACKEND_INDEX] {backend.name} reported {len(entries)} models"
        )

        by_identity = self._group_by_identity(entries, result)
        index = self._existing_by_identity()
        seen_model_ids: Set[str] = set()

        for identity, entry in by_identity.items():
            model = index.get(identity)

            if model is None:
                model = self._create_model(entry)
                result.created += 1
                confidence = entry.confidence
            else:
                result.matched += 1
                self._check_size(model, entry, backend.backend_id, result)
                confidence = self._resolve_confidence(model, entry, backend.backend_id, result)

            seen_model_ids.add(model.id)
            self.availability.upsert(ModelAvailability(
                id=None,
                model_id=model.id,
                backend_id=backend.backend_id,
                ref=entry.ref,
                size=entry.size,
                confidence=confidence,
                digest=entry.sha256,
            ))

        # A model removed from the backend must stop being offered. Leaving the row would
        # mean the picker keeps listing it and the generation fails inside the engine.
        result.removed = self.availability.delete_for_backend(
            backend.backend_id, keep_model_ids=seen_model_ids
        )

        logger.info(
            f"[BACKEND_INDEX] {backend.name}: {result.created} new, {result.matched} matched, "
            f"{result.removed} stale removed, {len(result.size_conflicts)} size conflicts"
        )
        return result

    def _group_by_identity(
        self, entries: List[BackendModel], result: IndexResult
    ) -> Dict[tuple, BackendModel]:
        """One entry per identity. Distinct sizes under one name are genuinely ambiguous.

        `deduplicate()` has already collapsed identical (name, size) pairs, so anything
        left over with a repeated identity has conflicting sizes *within a single backend*.
        Keep the first and say so, rather than picking silently.
        """
        grouped: Dict[tuple, BackendModel] = {}
        for entry in entries:
            if entry.identity in grouped:
                existing = grouped[entry.identity]
                if existing.size != entry.size:
                    result.ambiguous.append(
                        f"{entry.model_type}/{entry.filename} "
                        f"(sizes {existing.size} and {entry.size}); kept {existing.ref}"
                    )
                continue
            grouped[entry.identity] = entry
        return grouped

    def _check_size(
        self,
        model,
        entry: BackendModel,
        backend_id: str,
        result: IndexResult,
    ) -> None:
        """Compare, don't key on. A disagreement is reported, never used to split rows."""
        if entry.size is None or model.file_size is None:
            return
        if entry.size == model.file_size:
            return

        result.size_conflicts.append(SizeConflict(
            model_type=entry.model_type,
            filename=entry.filename,
            known_size=model.file_size,
            reported_size=entry.size,
            backend_id=backend_id,
        ))
        logger.warning(
            f"[BACKEND_INDEX] Size conflict for {entry.model_type}/{entry.filename}: "
            f"known {model.file_size} bytes, backend {backend_id} reports {entry.size}. "
            f"Treating as the same model - a quantised copy under the same filename would "
            f"generate with different weights depending on the backend."
        )

    def _resolve_confidence(
        self,
        model,
        entry: BackendModel,
        backend_id: str,
        result: IndexResult,
    ) -> str:
        """What this availability row's confidence should be, given what the backend
        just proved about its own copy.

        Silent by default: no digest from the backend, no canonical digest on the
        model yet, or a directory-model fingerprint (never a content hash - see
        101_add_model_is_directory.py) all fall through to `entry.confidence`
        unchanged. Only an actual disagreement between two real content digests
        downgrades to CONFIDENCE_CONFLICT.
        """
        if not entry.sha256 or not model.sha256 or getattr(model, "is_directory", False):
            return entry.confidence

        if entry.sha256 == model.sha256:
            return entry.confidence

        result.digest_conflicts.append(DigestConflict(
            model_type=entry.model_type,
            filename=entry.filename,
            known_digest=model.sha256,
            reported_digest=entry.sha256,
            backend_id=backend_id,
        ))
        logger.error(
            f"[BACKEND_INDEX] Digest conflict for {entry.model_type}/{entry.filename} on "
            f"backend {backend_id}: expected {model.sha256[:12]}…, backend reports "
            f"{entry.sha256[:12]}…. This backend's copy will not be routed to until "
            f"re-indexed with matching bytes."
        )
        return CONFIDENCE_CONFLICT

    def _existing_by_identity(self) -> Dict[tuple, object]:
        models = self.models.get_all(include_providers=False, include_tags=False)
        return {(m.model_type, m.filename): m for m in models}

    def _create_model(self, entry: BackendModel):
        from src.features.models.records import Model
        from src.platform.util.ids import generate_ulid

        model = Model(
            id=generate_ulid(),
            filename=entry.filename,
            file_path=self._local_path(entry),
            file_size=entry.size,
            sha256=entry.sha256,
            model_type=entry.model_type,
        )
        return self.models.create(model)

    @staticmethod
    def _local_path(entry: BackendModel) -> Optional[str]:
        """`file_path` only means something for a file on this host.

        A native ref *is* the local path. A remote ref is a name in someone else's
        namespace, so the column stays NULL - which is exactly what migration 074
        relaxed `NOT NULL` to permit.
        """
        from pathlib import Path
        try:
            return entry.ref if Path(entry.ref).is_file() else None
        except OSError:
            return None


backend_model_indexer = BackendModelIndexer()
