"""Free-text search across every phrasebook category and value."""
from typing import Any, Dict

from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)


def find_phrasebook(
    category_repository: PhrasebookCategoryRepository,
    value_repository: PhrasebookValueRepository,
    user_id: str,
    query: str,
    limit_per_kind: int = 50,
) -> Dict[str, Any]:
    """Case-insensitive substring search over categories (name, path,
    description) and values (label, value text), inactive rows included.
    A blank query yields an empty result rather than an error."""
    trimmed = (query or "").strip()
    if not trimmed:
        return {
            "query": trimmed,
            "categories": [],
            "values": [],
            "total_categories": 0,
            "total_values": 0,
        }

    limit = max(1, limit_per_kind)
    categories = category_repository.find_by_text(user_id, trimmed, limit)
    values = value_repository.find_by_text(user_id, trimmed, limit)
    return {
        "query": trimmed,
        "categories": [category.model_dump() for category in categories],
        "values": values,
        "total_categories": len(categories),
        "total_values": len(values),
    }
