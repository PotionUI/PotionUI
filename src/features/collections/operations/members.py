"""
Add/remove generations, library uploads, and saved prompts to/from a collection.

Module-level functions, `CollectionRepository` as the leading arg - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found" (the controller converts that to an HTTP response).
"""
from typing import List

from src.features.collections.operations.reads import get_collection
from src.features.collections.repository import CollectionRepository


def add_members(collection_repository: CollectionRepository, collection_id: str, generation_ids: List[str], user_id: str, scope: str) -> int:
    """
    Add generations to a collection owned by the user, within scope.

    Duplicate memberships are ignored. Returns the number newly added.

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    # Ownership + scope check (also raises if not found)
    get_collection(collection_repository, collection_id, user_id, scope)
    return collection_repository.add_members(collection_id, generation_ids, user_id, scope)


def remove_members(collection_repository: CollectionRepository, collection_id: str, generation_ids: List[str], user_id: str, scope: str) -> int:
    """
    Remove generations from a collection owned by the user, within scope.

    Returns the number of memberships removed.

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    # Ownership + scope check (also raises if not found)
    get_collection(collection_repository, collection_id, user_id, scope)
    return collection_repository.remove_members(collection_id, generation_ids)


def add_upload_members(collection_repository: CollectionRepository, collection_id: str, upload_ids: List[str], user_id: str, scope: str) -> int:
    """
    Add library uploads to a collection owned by the user, within scope.

    Uploads the user does not own are skipped, not reported - see
    `CollectionRepository.add_upload_members`. Returns the number newly added.

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    # Ownership + scope check (also raises if not found)
    get_collection(collection_repository, collection_id, user_id, scope)
    return collection_repository.add_upload_members(collection_id, upload_ids, user_id, scope)


def remove_upload_members(collection_repository: CollectionRepository, collection_id: str, upload_ids: List[str], user_id: str, scope: str) -> int:
    """
    Remove library uploads from a collection owned by the user, within scope.

    Returns the number of memberships removed.

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    # Ownership + scope check (also raises if not found)
    get_collection(collection_repository, collection_id, user_id, scope)
    return collection_repository.remove_upload_members(collection_id, upload_ids)


def add_prompt_members(collection_repository: CollectionRepository, collection_id: str, prompt_ids: List[str], user_id: str, scope: str) -> int:
    """
    Add saved prompts to a collection owned by the user, within scope.

    Prompts the user does not own are skipped, not reported - see
    `CollectionRepository.add_prompt_members`. Returns the number newly added.

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    # Ownership + scope check (also raises if not found)
    get_collection(collection_repository, collection_id, user_id, scope)
    return collection_repository.add_prompt_members(collection_id, prompt_ids, user_id, scope)


def remove_prompt_members(collection_repository: CollectionRepository, collection_id: str, prompt_ids: List[str], user_id: str, scope: str) -> int:
    """
    Remove saved prompts from a collection owned by the user, within scope.

    Returns the number of memberships removed.

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    # Ownership + scope check (also raises if not found)
    get_collection(collection_repository, collection_id, user_id, scope)
    return collection_repository.remove_prompt_members(collection_id, prompt_ids)
