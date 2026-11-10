import logging
from typing import Any, Callable, Dict, List, Mapping

from src.features.collections.repository import CollectionRepository
from src.features.generation.exceptions import GenerationException
from src.platform.http.errors import InvalidInputError, NotFoundError

logger = logging.getLogger(__name__)

EntryDeleter = Callable[[str, str], Any]

_FALLBACK_REASON = "This item could not be deleted"


def delete_entries(
    collection_repository: CollectionRepository,
    deleters: Mapping[str, EntryDeleter],
    collection_id: str,
    user_id: str,
) -> Dict[str, Any]:
    scope = collection_repository.scope_of(collection_id, user_id)
    if scope is None:
        raise NotFoundError("collection_not_found", "Collection not found or access denied")
    delete = deleters.get(scope)
    if delete is None:
        raise InvalidInputError(
            "collection_scope_unsupported",
            "Only History and Library collections can have their entries deleted",
        )

    member_ids = collection_repository.member_ids(collection_id, scope)
    deleted = 0
    blocked: List[Dict[str, str]] = []
    for member_id in member_ids:
        try:
            delete(member_id, user_id)
        except (ValueError, GenerationException) as exc:
            blocked.append({"id": member_id, "reason": str(exc) or _FALLBACK_REASON})
            continue
        except Exception:
            logger.exception("Deleting collection %s entry %s failed", collection_id, member_id)
            blocked.append({"id": member_id, "reason": _FALLBACK_REASON})
            continue
        deleted += 1

    return {"deleted": deleted, "blocked": blocked, "total": len(member_ids)}
