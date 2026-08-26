"""Model index operations: dispatch onto the focused role classes.

Module-level functions, `ModelIndexCollaborators` as their leading arg - no
class holds the role objects together (see `collaborators.py`'s docstring).
Almost every operation here is a one-line dispatch onto the right role
object; `list_model_previews_for_user` is the one exception with real glue
logic (the house 404-not-403 idiom - see its docstring).
"""

from typing import Any, Dict, List, Optional

from src.features.models.catalog import ListModelsParams
from src.features.models.collaborators import ModelIndexCollaborators
from src.features.models.exceptions import ModelAccessDeniedException, ModelNotFoundException
from src.platform.security.user import User

__all__ = [
    "list_models", "get_model_availability", "get_model_stats", "get_model_types",
    "get_model_by_hash", "get_model_by_id", "get_model_generations",
    "start_indexing", "run_indexing", "cleanup_deleted_models", "count_unindexed",
    "get_models_location", "apply_models_location",
    "delete_model", "update_model_tags", "update_model_description",
    "update_model_prompting_guidance", "update_model_metadata", "update_model_preview",
    "list_model_previews", "list_model_previews_for_user", "add_model_preview",
    "delete_model_preview", "reorder_model_previews",
    "fetch_provider_info", "run_provider_fetch",
    "get_user_model_assignments", "assign_model_to_user", "unassign_model_from_user",
    "get_model_assignments", "get_model_assignment_summary",
    "start_thumbnail_generation", "run_thumbnail_generation",
    "start_download_and_index", "run_download_and_index",
]


# ========== Catalog / queries ==========

def list_models(collaborators: ModelIndexCollaborators, params: ListModelsParams, user: User) -> Dict[str, Any]:
    return collaborators.catalog.list_models(params, user)


def get_model_availability(collaborators: ModelIndexCollaborators, model_id: str) -> Dict[str, Any]:
    return collaborators.catalog.get_model_availability(model_id)


def get_model_stats(collaborators: ModelIndexCollaborators) -> Dict[str, Any]:
    return collaborators.catalog.get_model_stats()


def get_model_types(
    collaborators: ModelIndexCollaborators, user: User, user_scoped: bool = False, include_empty: bool = False
) -> Dict[str, Any]:
    return collaborators.catalog.get_model_types(user, user_scoped, include_empty)


def get_model_by_hash(collaborators: ModelIndexCollaborators, sha256: str) -> Dict[str, Any]:
    return collaborators.catalog.get_model_by_hash(sha256)


def get_model_by_id(
    collaborators: ModelIndexCollaborators, model_id: str, user: Optional[User] = None, admin: bool = False
) -> Dict[str, Any]:
    return collaborators.catalog.get_model_by_id(model_id, user, admin)


def get_model_generations(
    collaborators: ModelIndexCollaborators, model_id: str, user: User, limit: int = 20, offset: int = 0
) -> Dict[str, Any]:
    return collaborators.catalog.get_model_generations(model_id, user, limit, offset)


# ========== Indexing ==========

def start_indexing(collaborators: ModelIndexCollaborators) -> Dict[str, Any]:
    return collaborators.indexing.start_indexing()


def run_indexing(collaborators: ModelIndexCollaborators) -> None:
    return collaborators.indexing.run_indexing()


def cleanup_deleted_models(collaborators: ModelIndexCollaborators) -> Dict[str, Any]:
    return collaborators.indexing.cleanup_deleted_models()


def count_unindexed(collaborators: ModelIndexCollaborators) -> Dict[str, Any]:
    return collaborators.indexing.count_unindexed()


# ========== Models location ==========

def get_models_location(collaborators: ModelIndexCollaborators) -> Dict[str, Any]:
    return collaborators.location.get_config()


def apply_models_location(
    collaborators: ModelIndexCollaborators, external_path: str, overrides: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    return collaborators.location.apply(external_path, overrides)


# ========== Metadata editing ==========

def delete_model(collaborators: ModelIndexCollaborators, model_id: str) -> Dict[str, Any]:
    return collaborators.metadata.delete_model(model_id)


def update_model_tags(collaborators: ModelIndexCollaborators, model_id: str, tag_ids: List[str]) -> Dict[str, Any]:
    return collaborators.metadata.update_model_tags(model_id, tag_ids)


def update_model_description(collaborators: ModelIndexCollaborators, model_id: str, description: str) -> Dict[str, Any]:
    return collaborators.metadata.update_model_description(model_id, description)


def update_model_prompting_guidance(
    collaborators: ModelIndexCollaborators, model_id: str, prompting_guidance: str
) -> Dict[str, Any]:
    return collaborators.metadata.update_model_prompting_guidance(model_id, prompting_guidance)


def update_model_metadata(collaborators: ModelIndexCollaborators, model_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    return collaborators.metadata.update_model_metadata(model_id, values)


def update_model_preview(
    collaborators: ModelIndexCollaborators,
    model_id: str,
    preview_input: Optional[Dict[str, Any]],
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    return collaborators.metadata.update_model_preview(model_id, preview_input, user_id)


def list_model_previews(collaborators: ModelIndexCollaborators, model_id: str) -> List[Dict[str, Any]]:
    return collaborators.metadata.list_model_previews(model_id)


def list_model_previews_for_user(
    collaborators: ModelIndexCollaborators, model_id: str, user: User
) -> List[Dict[str, Any]]:
    """List a model's previews for any caller who can reach the model.

    Mirrors `get_model_generations`'s access check instead of the
    admin gate. A denied and a missing model both surface as
    ModelNotFoundException (house 404-not-403 idiom) - the caller can't
    use this endpoint to probe which model ids exist.
    """
    try:
        collaborators.access.verify_model_access(model_id, user)
    except ModelAccessDeniedException:
        raise ModelNotFoundException(f"Model '{model_id}' not found")
    return collaborators.metadata.list_model_previews(model_id)


def add_model_preview(
    collaborators: ModelIndexCollaborators, model_id: str, preview_input: Dict[str, Any], user_id: Optional[str] = None
) -> Dict[str, Any]:
    return collaborators.metadata.add_model_preview(model_id, preview_input, user_id)


def delete_model_preview(collaborators: ModelIndexCollaborators, model_id: str, preview_id: str) -> Dict[str, Any]:
    return collaborators.metadata.delete_model_preview(model_id, preview_id)


def reorder_model_previews(
    collaborators: ModelIndexCollaborators, model_id: str, ordered_ids: List[str]
) -> Dict[str, Any]:
    return collaborators.metadata.reorder_model_previews(model_id, ordered_ids)


# ========== Provider info ==========

def fetch_provider_info(
    collaborators: ModelIndexCollaborators,
    provider: str,
    model_ids: Optional[List[str]] = None,
    force_refresh: bool = False
) -> Dict[str, Any]:
    return collaborators.provider_info.fetch_provider_info(provider, model_ids, force_refresh)


async def run_provider_fetch(
    collaborators: ModelIndexCollaborators,
    provider: str,
    model_ids: Optional[List[str]] = None,
    force_refresh: bool = False
) -> None:
    return await collaborators.provider_info.run_provider_fetch(provider, model_ids, force_refresh)


# ========== Assignments ==========

def get_user_model_assignments(collaborators: ModelIndexCollaborators, user_id: str) -> Dict[str, Any]:
    return collaborators.assignments.get_user_model_assignments(user_id)


def assign_model_to_user(collaborators: ModelIndexCollaborators, model_id: str, user_id: str) -> Dict[str, Any]:
    return collaborators.assignments.assign_model_to_user(model_id, user_id)


def unassign_model_from_user(collaborators: ModelIndexCollaborators, model_id: str, user_id: str) -> Dict[str, Any]:
    return collaborators.assignments.unassign_model_from_user(model_id, user_id)


def get_model_assignments(collaborators: ModelIndexCollaborators, model_id: str) -> Dict[str, Any]:
    return collaborators.assignments.get_model_assignments(model_id)


def get_model_assignment_summary(collaborators: ModelIndexCollaborators) -> Dict[str, Dict[str, int]]:
    return collaborators.assignments.get_assignment_summary()


# ========== Jobs ==========

def start_thumbnail_generation(
    collaborators: ModelIndexCollaborators, model_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    return collaborators.jobs.start_thumbnail_generation(model_ids)


async def run_thumbnail_generation(collaborators: ModelIndexCollaborators, model_ids: Optional[List[str]] = None) -> None:
    return await collaborators.jobs.run_thumbnail_generation(model_ids)


def start_download_and_index(
    collaborators: ModelIndexCollaborators,
    name: str,
    link: str,
    size: str,
    sha256: str,
    model_type: str = 'checkpoint'
) -> Dict[str, Any]:
    return collaborators.jobs.start_download_and_index(name, link, size, sha256, model_type)


async def run_download_and_index(
    collaborators: ModelIndexCollaborators,
    name: str,
    link: str,
    sha256: str,
    model_type: str = 'checkpoint'
) -> None:
    return await collaborators.jobs.run_download_and_index(name, link, sha256, model_type)
