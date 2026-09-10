"""Deleting generations by admin-chosen criteria, across every user.

The repository finds candidate (id, user_id) pairs; deletion is then grouped
by owner and run through `GenerationHistoryFacade.bulk_delete` per owner, so
the normal ownership check, file cleanup and hooks all still apply.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GenerationDeleteCriteria:
    older_than_days: Optional[int] = None
    without_media: bool = False
    statuses: Optional[List[str]] = None
    keep_favorites: bool = True

    def is_empty(self) -> bool:
        """No criteria narrows the candidate set - deleting under this would
        sweep every non-favorite, non-in-flight generation for every user."""
        return self.older_than_days is None and not self.without_media and not self.statuses


def _find(repository, criteria: GenerationDeleteCriteria) -> List[tuple]:
    return repository.find_for_housekeeping(
        older_than_days=criteria.older_than_days,
        without_media=criteria.without_media,
        statuses=criteria.statuses,
        keep_favorites=criteria.keep_favorites,
    )


def preview_generations(repository, criteria: GenerationDeleteCriteria) -> int:
    """How many generations `criteria` matches right now."""
    return len(_find(repository, criteria))


def delete_generations(repository, history_facade, criteria: GenerationDeleteCriteria) -> Dict[str, Any]:
    """Delete every generation matching `criteria`, for every owner."""
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
