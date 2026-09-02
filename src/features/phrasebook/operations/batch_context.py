"""The `PhrasebookBatchContext` batch operations run against: the user's
values and categories through the repositories, one transaction per write."""
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.platform.plugins.phrasebook_ops import BatchOperationError, PhrasebookBatchContext


def owned_ids(value_repository: PhrasebookValueRepository, value_ids: Sequence[str], user_id: str):
    ids = list(dict.fromkeys(value_ids))
    if not ids:
        raise BatchOperationError("empty_selection", "No values selected")
    owned = value_repository.get_many(ids, user_id)
    found = {v.id for v in owned}
    missing = [i for i in ids if i not in found]
    if missing:
        raise BatchOperationError("unknown_values", f"Unknown values: {', '.join(missing)}")
    return owned


class RepositoryBatchContext(PhrasebookBatchContext):
    def __init__(
        self,
        value_repository: PhrasebookValueRepository,
        category_repository: PhrasebookCategoryRepository,
        user_id: str,
        plugins=None,
    ):
        self._values = value_repository
        self._categories = category_repository
        self.user_id = user_id
        self.plugins = plugins

    def values(self, value_ids: Sequence[str]) -> List[Dict[str, Any]]:
        return [v.model_dump() for v in owned_ids(self._values, value_ids, self.user_id)]

    def category(self, category_id: str) -> Optional[Dict[str, Any]]:
        category = self._categories.get_by_id(category_id, self.user_id)
        return category.model_dump() if category else None

    def update_value_texts(self, rows: Sequence[Tuple[str, str, str]]) -> None:
        if not rows:
            return
        owned_ids(self._values, [r[0] for r in rows], self.user_id)
        self._values.update_texts_bulk(self.user_id, list(rows))

    def set_active(self, value_ids: Sequence[str], is_active: bool) -> None:
        ids = [v.id for v in owned_ids(self._values, value_ids, self.user_id)]
        self._values.update_active_state_bulk(ids, self.user_id, is_active)

    def move(self, value_ids: Sequence[str], category_id: str) -> List[Dict[str, Any]]:
        values = owned_ids(self._values, value_ids, self.user_id)
        if not self._categories.get_by_id(category_id, self.user_id):
            raise BatchOperationError("unknown_category", f"Unknown category: {category_id}")
        next_order = self._values.max_sort_order(category_id, self.user_id) + 1
        moves = []
        for value in values:
            if value.category_id == category_id:
                continue
            moves.append((value.id, category_id, next_order))
            next_order += 1
        self._values.move_bulk(self.user_id, moves)
        return [v.model_dump() for v in self._values.get_many([v.id for v in values], self.user_id)]

    def delete(self, value_ids: Sequence[str]) -> None:
        ids = [v.id for v in owned_ids(self._values, value_ids, self.user_id)]
        self._values.delete_bulk(ids, self.user_id)
