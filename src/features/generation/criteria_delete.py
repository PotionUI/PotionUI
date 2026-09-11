"""Deleting generations by criteria - tags (ALL of them), age, a created date
range, status, presence of media, with a favorites guard.

Scoped to one owner (`user_id` set, the History "Delete by criteria" flow) or
every user (`user_id=None`, admin housekeeping). The repository finds
candidate (id, user_id) pairs; deletion is then grouped by owner and run
through `GenerationHistoryFacade.bulk_delete` per owner, so the normal
ownership check, file cleanup and hooks all still apply.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GenerationDeleteCriteria:
    user_id: Optional[str] = None
    tag_ids: Optional[List[str]] = None
    older_than_days: Optional[int] = None
    created_from: Optional[str] = None
    created_to: Optional[str] = None
    without_media: bool = False
    statuses: Optional[List[str]] = None
    keep_favorites: bool = True

    def is_empty(self) -> bool:
        """No criteria narrows the candidate set - deleting under this would
        sweep every non-favorite, non-in-flight generation in scope."""
        return (
            not self.tag_ids
            and self.older_than_days is None
            and self.created_from is None
            and self.created_to is None
            and not self.without_media
            and not self.statuses
        )


def _find(repository, criteria: GenerationDeleteCriteria) -> List[tuple]:
    return repository.find_by_criteria(
        user_id=criteria.user_id,
        tag_ids=criteria.tag_ids,
        older_than_days=criteria.older_than_days,
        created_from=criteria.created_from,
        created_to=criteria.created_to,
        without_media=criteria.without_media,
        statuses=criteria.statuses,
        keep_favorites=criteria.keep_favorites,
    )


def preview_generations(repository, criteria: GenerationDeleteCriteria) -> int:
    """How many generations `criteria` matches right now."""
    return len(_find(repository, criteria))


def delete_generations(repository, history_facade, criteria: GenerationDeleteCriteria) -> Dict[str, Any]:
    """Delete every generation matching `criteria`, grouped by owner."""
    by_user: Dict[str, List[str]] = {}
    for generation_id, user_id in _find(repository, criteria):
        if not user_id:
            continue
        by_user.setdefault(user_id, []).append(generation_id)

    deleted_count = 0
    files_deleted = 0
    for user_id, generation_ids in by_user.items():
        summary = history_facade.bulk_delete(generation_ids, user_id)
        deleted_count += summary["deleted_count"]
        files_deleted += summary["total_files_deleted"]

    return {"deleted_count": deleted_count, "files_deleted": files_deleted}
