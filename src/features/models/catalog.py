"""Read-side of the model index: listing, lookup, availability and statistics.

Every method here is a query with access control applied; none of them mutate a
model. Writes live in the metadata, assignment, indexing and job role classes.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.exceptions import ModelNotFoundException
from src.features.models.indexer import ModelScanner
from src.features.models.jobs import TYPE_DIR_MAP
from src.features.models.repository import ModelRepository
from src.features.tags.repository import tag_repo
from src.features.models.availability_repository import model_availability_repo
from src.platform.security.user import User, AccountType

logger = logging.getLogger(__name__)


@dataclass
class ListModelsParams:
    """Parameters for listing models."""
    model_type: Optional[str] = None
    tag_ids: Optional[List[str]] = None
    search: Optional[str] = None
    sort_by: str = "indexed_at"
    sort_order: str = "desc"
    limit: Optional[int] = 20
    offset: int = 0
    include_tags: bool = True
    all_models: bool = False
    assignment_filter: Optional[str] = None
    assigned_user_id: Optional[str] = None
    assigned_group_id: Optional[str] = None
    favorites_only: bool = False
    collection_id: Optional[str] = None
    in_any_collection: bool = False


class ModelCatalog:
    """Answers questions about indexed models with per-user access control.

    Reads the model repository and the availability index; leans on
    ModelAccessPolicy for visibility and on the directory scanner for the
    aggregate indexing statistics.
    """

    def __init__(
        self,
        model_repository: ModelRepository,
        access_policy: ModelAccessPolicy,
        scanner: ModelScanner,
        user_attribute_repository: Optional[UserModelAttributeRepository] = None,
    ):
        self.model_repo = model_repository
        self.access_policy = access_policy
        self.scanner = scanner
        self.user_attributes = user_attribute_repository or UserModelAttributeRepository()

    def list_models(self, params: ListModelsParams, user: User) -> Dict[str, Any]:
        """List models with filtering, pagination, and access control."""
        # Determine allowed model IDs based on user permissions
        allowed_model_ids = self.access_policy.get_allowed_model_ids(user, params.all_models)

        # Get models
        models = self.model_repo.get_all(
            limit=params.limit,
            offset=params.offset,
            model_type=params.model_type,
            tag_ids=params.tag_ids,
            search=params.search,
            sort_by=params.sort_by,
            sort_order=params.sort_order,
            include_providers=True,
            include_tags=params.include_tags,
            allowed_model_ids=allowed_model_ids,
            assignment_filter=params.assignment_filter,
            assigned_user_id=params.assigned_user_id,
            assigned_group_id=params.assigned_group_id,
            library_user_id=user.id,
            favorites_only=params.favorites_only,
            collection_id=params.collection_id,
            in_any_collection=params.in_any_collection
        )

        # Get total count for pagination
        total_count = self.model_repo.count_total(
            tag_ids=params.tag_ids,
            search=params.search,
            model_type=params.model_type,
            allowed_model_ids=allowed_model_ids,
            assignment_filter=params.assignment_filter,
            assigned_user_id=params.assigned_user_id,
            assigned_group_id=params.assigned_group_id,
            library_user_id=user.id,
            favorites_only=params.favorites_only,
            collection_id=params.collection_id,
            in_any_collection=params.in_any_collection
        )

        # Get statistics
        stats = self.get_model_stats()

        # Where a model lives, how big it is and which backends hold it are operational
        # facts. A generating user needs none of them; an admin needs all of them.
        is_admin = user.account_type == AccountType.ADMIN

        models_data = [
            model.to_dict(
                include_providers=True,
                include_tags=params.include_tags,
                admin=is_admin,
            )
            for model in models
        ]

        # Per-user attribute overlay (e.g. a per-user default strength) - the
        # LoRA picker and model library both read the model dicts this returns,
        # so this is where every user-scoped model response picks it up.
        overlays = self.user_attributes.get_maps(user.id, [m.id for m in models])
        for entry in models_data:
            entry["user_model_metadata"] = overlays.get(entry["id"], {})

        result = {
            "models": models_data,
            "total": total_count,
            "pagination": {
                "limit": params.limit,
                "offset": params.offset,
                "has_more": len(models) == params.limit
            },
            "stats": stats,
        }

        if is_admin:
            # Which backends can load each model. One query for the page, never per row.
            # An empty list means "nothing has been indexed yet", not "available nowhere":
            # callers must read `availability_indexed` before drawing that conclusion.
            by_model = model_availability_repo.backend_ids_by_model([m.id for m in models])
            for entry in models_data:
                entry["backend_ids"] = sorted(by_model.get(entry["id"], []))
            result["availability_indexed"] = model_availability_repo.has_any()

        return result

    def get_model_availability(self, model_id: str) -> Dict[str, Any]:
        """Where this model can be loaded, and under what name on each backend.

        `ref` differs per engine (a path natively, a bare name on a ComfyUI server), and
        `size` may disagree between backends - a quantised copy that kept its filename.
        Both are surfaced rather than reconciled; see docs/models.md.
        """
        from src.features.backends.repository import backend_repo

        rows = model_availability_repo.get_for_model(model_id)
        backends = {b.id: b for b in backend_repo.get_all()}

        entries = []
        for row in rows:
            backend = backends.get(row.backend_id)
            entry = row.to_dict()
            entry["backend_name"] = backend.name if backend else row.backend_id
            entry["engine"] = backend.engine if backend else None
            entries.append(entry)

        entries.sort(key=lambda e: (e["engine"] or "", e["backend_name"]))

        sizes = {e["size"] for e in entries if e["size"] is not None}
        return {
            "model_id": model_id,
            "availability": entries,
            "indexed": model_availability_repo.has_any(),
            # Same filename, different byte counts: the backends hold different weights.
            "size_conflict": len(sizes) > 1,
            # At least one backend's own copy disagrees with the model's canonical
            # digest - that row is excluded from routing (see CONFIDENCE_CONFLICT).
            "digest_conflict": any(e["confidence"] == "conflict" for e in entries),
        }

    def get_model_stats(self) -> Dict[str, Any]:
        """Aggregate indexing statistics (counts and sizes per type)."""
        return self.scanner.get_indexing_status()

    def _type_directory(self, model_type: str) -> str:
        """Where `model_type` actually lives under the configured model depot.

        Uses the scanner's resolved `models_dir` (settings-backed, not the
        process CWD) joined with `TYPE_DIR_MAP`'s subdir - the same mapping
        `DownloadManager.queue_model_download` resolves a `model_type` through.
        """
        subdir = TYPE_DIR_MAP.get(model_type, model_type)
        return str(self.scanner.models_dir / subdir)

    def get_model_types(
        self,
        user: User,
        user_scoped: bool = False,
        include_empty: bool = False,
    ) -> Dict[str, Any]:
        """Available model types and their counts.

        When `user_scoped` (or the caller is not an admin), counts only the models
        assigned to the user.

        When `include_empty` is true - and only for an unscoped admin request -
        the response also includes every known model type (per
        `ModelScanner.MODEL_TYPE_MAPPING`) that has zero indexed models, with
        `count: 0`. This lets callers like the "Add by URL" downloader offer a
        type that has no rows indexed yet. Synthetic zero-count types must never
        leak to a user_scoped or non-admin caller - their model access would hide
        such types anyway, and showing them would be misleading.
        """
        is_admin_unscoped = not user_scoped and user.account_type == AccountType.ADMIN
        if user_scoped or user.account_type != AccountType.ADMIN:
            allowed_model_ids = self.model_repo.get_available_model_ids_for_user(user.id)
        else:
            allowed_model_ids = None

        type_counts = self.model_repo.count_by_type(allowed_model_ids=allowed_model_ids)
        type_sizes = self.model_repo.get_total_size_by_type()

        types = []
        for model_type, count in type_counts.items():
            size_bytes = type_sizes.get(model_type, 0)
            types.append({
                "type": model_type,
                "directory": self._type_directory(model_type),
                "count": count,
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / (1024 * 1024), 2) if size_bytes > 0 else 0,
                "size_gb": round(size_bytes / (1024 * 1024 * 1024), 2) if size_bytes > 0 else 0
            })

        if include_empty and is_admin_unscoped:
            known_types = set(self.scanner.MODEL_TYPE_MAPPING.values())
            for model_type in sorted(known_types - set(type_counts.keys())):
                types.append({
                    "type": model_type,
                    "directory": self._type_directory(model_type),
                    "count": 0,
                    "size_bytes": 0,
                    "size_mb": 0,
                    "size_gb": 0
                })

        return {
            "types": types,
            "total_types": len(types)
        }

    def get_model_by_hash(self, sha256: str) -> Dict[str, Any]:
        """Look up a model by its SHA256 hash. Raises ModelNotFoundException if absent."""
        model = self.model_repo.get_by_sha256(sha256, include_providers=False)
        if not model:
            raise ModelNotFoundException(f"Model with hash '{sha256}' not found")

        return {"model": model.to_dict(include_providers=False)}

    def get_model_by_id(self, model_id: str, user: Optional[User] = None, admin: bool = False) -> Dict[str, Any]:
        """Look up a model by ID. `admin` includes operational fields (path, size, hash).

        `user` is optional (the LLM tool context looks models up with none) - without
        it, `custom_name`/`is_favorite` fall back to their defaults rather than the
        caller's actual per-user state.
        """
        model = self.model_repo.get_by_id(
            model_id, include_providers=False, library_user_id=user.id if user else None
        )
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        data = model.to_dict(include_providers=False, admin=admin)
        data["user_model_metadata"] = self.user_attributes.get_map(user.id, model_id) if user else {}
        return {"model": data}

    def get_model_generations(
        self,
        model_id: str,
        user: User,
        limit: int = 20,
        offset: int = 0
    ) -> Dict[str, Any]:
        """Generations that used a specific model, gated by the caller's access.

        Raises ModelNotFoundException / ModelAccessDeniedException via the access policy.
        """
        # Verify access (throws if denied)
        self.access_policy.verify_model_access(model_id, user)

        from src.features.generation.model_repository import generation_model_repo
        generations, total = generation_model_repo.get_generations_by_model(
            model_id=model_id,
            user_id=user.id,
            limit=limit,
            offset=offset
        )

        # The details modal renders (and can edit) tags, so they must be real rather than
        # an empty list: serializing `tags: []` for a generation that has tags would let
        # a save wipe them. Loaded per row, matching generation_repository.get_all.
        for generation in generations:
            generation.tags = tag_repo.get_generation_tags(generation.id)

        return {
            "generations": [
                g.to_dict(include_files=True, include_tags=True) for g in generations
            ],
            "total": total,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": (offset + limit) < total
            }
        }
