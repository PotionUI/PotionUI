"""Read-side of the model index: listing, lookup, availability and statistics.

Every method here is a query with access control applied; none of them mutate a
model. Writes live in the metadata, assignment, indexing and job role classes.
"""

from pathlib import Path
import logging
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.attributes.user_repository import UserModelAttributeRepository
from src.features.models.exceptions import ModelNotFoundException
from src.features.models.indexer import ModelScanner
from src.features.models.locator import ModelLocator
from src.features.models.repository import ModelRepository
from src.features.models.search_filter import USAGE_SORT_FIELDS, ModelSearchFilter
from src.features.tags.repository import tag_repo
from src.features.models.availability_repository import model_availability_repo
from src.platform.filesystem.model_roots import ModelRootError
from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY, UNDEFINED_MODEL_TYPE
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
    search_filter: Optional[ModelSearchFilter] = None


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
        locator: Optional["ModelLocator"] = None,
    ):
        self.model_repo = model_repository
        self.access_policy = access_policy
        self.scanner = scanner
        self.user_attributes = user_attribute_repository or UserModelAttributeRepository()
        if locator is not None:
            self.locator = locator
        elif scanner is not None:
            self.locator = ModelLocator(scanner.resolver)
        else:
            self.locator = None

    def list_models(self, params: ListModelsParams, user: User) -> Dict[str, Any]:
        """List models with filtering, pagination, and access control."""
        # Determine allowed model IDs based on user permissions
        allowed_model_ids = self.access_policy.get_allowed_model_ids(user, params.all_models)
        is_admin = user.account_type == AccountType.ADMIN
        search_filter = self._visible_search_filter(params.search_filter, is_admin)
        sort_by = params.sort_by
        if not is_admin and sort_by in USAGE_SORT_FIELDS:
            sort_by = "indexed_at"

        # Get models
        models = self.model_repo.get_all(
            limit=params.limit,
            offset=params.offset,
            model_type=params.model_type,
            tag_ids=params.tag_ids,
            search=params.search,
            sort_by=sort_by,
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
            in_any_collection=params.in_any_collection,
            search_filter=search_filter,
            include_usage=is_admin,
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
            in_any_collection=params.in_any_collection,
            search_filter=search_filter,
        )

        # Get statistics
        stats = self.get_model_stats()

        # Where a model lives, how big it is and which backends hold it are operational
        # facts. A generating user needs none of them; an admin needs all of them.

        if is_admin:
            try:
                summaries = self.locator.location_summaries()
            except Exception as exc:
                logger.warning(f"Could not compute model locations: {exc}")
                summaries = {}
            for model in models:
                summary = summaries.get(model.id)
                if summary:
                    model.location = summary["location"]
                    model.copies = summary["copies"]

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

    @staticmethod
    def _visible_search_filter(
        search_filter: Optional[ModelSearchFilter], is_admin: bool
    ) -> Optional[ModelSearchFilter]:
        if is_admin or search_filter is None:
            return search_filter
        return replace(search_filter, used="any", min_uses=None, last_used_from=None, last_used_to=None)

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

    def _type_directory(self, model_type: str) -> Optional[str]:
        type_dir = self._type_write_dir(model_type)
        return str(type_dir) if type_dir is not None else None

    def _type_write_dir(self, model_type: str) -> Optional[Path]:
        try:
            return self.scanner.resolver.write_dir(model_type).path
        except ModelRootError:
            return None

    def _type_subdirectories(self, model_type: str, max_depth: int = 4) -> List[str]:
        type_dir = self._type_write_dir(model_type)
        if type_dir is None or not type_dir.is_dir():
            return []
        found: List[str] = []

        def walk(directory: Path, depth: int) -> None:
            for entry in sorted(directory.iterdir(), key=lambda e: e.name):
                if not entry.is_dir() or entry.name.startswith('.'):
                    continue
                found.append(entry.relative_to(type_dir).as_posix())
                if depth < max_depth:
                    walk(entry, depth + 1)

        walk(type_dir, 1)
        return found

    def get_model_types(
        self,
        user: User,
        user_scoped: bool = False,
        include_empty: bool = False,
        facets: Optional[ListModelsParams] = None,
        include_tag_counts: bool = False,
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
        if not is_admin_unscoped:
            type_counts = {t: n for t, n in type_counts.items() if t != UNDEFINED_MODEL_TYPE}

        facet_filters = None
        if facets is not None:
            facet_filters = {
                "tag_ids": facets.tag_ids,
                "search": facets.search,
                "allowed_model_ids": allowed_model_ids,
                "assignment_filter": facets.assignment_filter,
                "assigned_user_id": facets.assigned_user_id,
                "assigned_group_id": facets.assigned_group_id,
                "library_user_id": user.id,
                "favorites_only": facets.favorites_only,
                "collection_id": facets.collection_id,
                "in_any_collection": facets.in_any_collection,
                "search_filter": self._visible_search_filter(
                    facets.search_filter, user.account_type == AccountType.ADMIN
                ),
            }
            matched_counts = self.model_repo.count_filtered_by_type(**facet_filters)
            if not is_admin_unscoped:
                matched_counts = {t: n for t, n in matched_counts.items() if t != UNDEFINED_MODEL_TYPE}
        else:
            matched_counts = type_counts

        types = []
        for model_type in type_counts:
            size_bytes = type_sizes.get(model_type, 0)
            types.append({
                "type": model_type,
                "directory": self._type_directory(model_type),
                "subdirectories": self._type_subdirectories(model_type),
                "count": matched_counts.get(model_type, 0),
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
                    "subdirectories": self._type_subdirectories(model_type),
                    "count": 0,
                    "size_bytes": 0,
                    "size_mb": 0,
                    "size_gb": 0
                })

        result: Dict[str, Any] = {
            "types": types,
            "total_types": len(types),
            "total": sum(matched_counts.values()),
        }
        if include_tag_counts:
            tag_filters = dict(facet_filters or {"allowed_model_ids": allowed_model_ids})
            if facets is not None and facets.model_type:
                tag_filters["model_type"] = facets.model_type
            result["tag_counts"] = self.model_repo.count_filtered_by_tag(**tag_filters)
        return result

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
            model_id, include_providers=admin, library_user_id=user.id if user else None
        )
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        locations = []
        if admin:
            try:
                locations = self.locator.locations(model_id)
            except Exception as exc:
                logger.warning(f"Could not compute locations for model '{model_id}': {exc}")
        if admin:
            winner = next((loc for loc in locations if loc.is_winner), None)
            present_count = sum(1 for loc in locations if loc.status == "present")
            model.copies = present_count
            if winner is not None:
                type_dir = MODEL_TYPE_TO_DIRECTORY.get(winner.model_type, winner.model_type)
                model.location = {
                    "root_id": winner.root_id,
                    "root_label": winner.root_label,
                    "logical_path": f"{type_dir}/{winner.rel_path}",
                    "path": str(winner.path) if winner.path is not None else None,
                }

        data = model.to_dict(include_providers=admin, admin=admin)
        if admin:
            data["locations"] = [
                {
                    "id": loc.id,
                    "root_id": loc.root_id,
                    "root_label": loc.root_label,
                    "model_type": loc.model_type,
                    "rel_path": loc.rel_path,
                    "path": str(loc.path) if loc.path is not None else None,
                    "status": loc.status,
                    "size": loc.size,
                    "sha256": loc.sha256,
                    "is_winner": loc.is_winner,
                }
                for loc in locations
            ]
        data["user_model_metadata"] = self.user_attributes.get_map(user.id, model_id) if user else {}
        self._attach_provider_mirrors(data, model.providers)
        return {"model": data}

    def _attach_provider_mirrors(self, data: Dict[str, Any], providers: List) -> None:
        entries = data.get("providers")
        if not entries:
            return

        from src.features.providers.registry import get_provider_registry

        registry = get_provider_registry()
        for entry, info in zip(entries, providers):
            provider = registry.get_provider(info.provider)
            page_url = provider.get_model_page_url(info.provider_model_id, info.provider_version_id) if provider else None
            parsed = urlparse(page_url) if page_url else None
            entry["provider_label"] = provider.provider_name if provider else info.provider
            entry["page_url"] = page_url if parsed and parsed.scheme in ("http", "https") else None

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
