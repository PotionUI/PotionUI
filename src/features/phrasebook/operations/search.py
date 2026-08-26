"""Search phrasebook suggestions by path prefix."""
from typing import Any, Dict

from src.features.phrasebook.dto import PhrasebookStateFilter
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)


def search_phrasebook(
    category_repository: PhrasebookCategoryRepository,
    value_repository: PhrasebookValueRepository,
    path: str,
    user_id: str,
    limit: int = 50,
    state_filter: PhrasebookStateFilter = PhrasebookStateFilter.ACTIVE,
) -> Dict[str, Any]:
    """
    Search for phrasebook suggestions by path.

    Args:
        category_repository: Category repository
        value_repository: Value repository
        path: The path to search for
        user_id: The user ID
        limit: Maximum number of values to return
        state_filter: Filter by active state (default: ACTIVE for chip editor usage)

    Returns:
        Dict containing current_category, child_categories, values, and counts
    """
    # Strip trailing dot if present (happens when typing after category selection)
    path_to_search = path.rstrip('.') if path else ''

    # First, check if this exact path exists as a category
    exact_category = category_repository.get_by_path(path_to_search, user_id) if path_to_search else None

    # For ACTIVE state filter, check if the exact category itself is active
    if exact_category and state_filter == PhrasebookStateFilter.ACTIVE and not exact_category.is_active:
        exact_category = None

    if exact_category:
        # Get direct child categories
        child_categories = category_repository.get_children(exact_category.id, user_id)
        # Filter by state if needed
        if state_filter == PhrasebookStateFilter.ACTIVE:
            child_categories = [c for c in child_categories if c.is_active]
        elif state_filter == PhrasebookStateFilter.INACTIVE:
            child_categories = [c for c in child_categories if not c.is_active]

        # Get values for this exact category with state filtering
        category_values = value_repository.get_by_category(exact_category.id, user_id, state_filter)

        return {
            "current_category": exact_category.model_dump(),
            "child_categories": [cat.model_dump() for cat in child_categories],
            "values": [val.model_dump() for val in category_values],
            "path": path,  # Keep original path for display
            "total_children": len(child_categories),
            "total_values": len(category_values),
        }
    else:
        # Path doesn't match exact category, search for prefix matches
        if path_to_search:
            matching_categories = category_repository.search_by_path_prefix(path_to_search, user_id)
        else:
            matching_categories = category_repository.get_children(None, user_id)

        # Filter by state if needed
        if state_filter == PhrasebookStateFilter.ACTIVE:
            matching_categories = [c for c in matching_categories if c.is_active]
        elif state_filter == PhrasebookStateFilter.INACTIVE:
            matching_categories = [c for c in matching_categories if not c.is_active]

        # For prefix search, also get values from matching categories with state filtering
        all_values = []
        if path_to_search:
            all_values = value_repository.search_by_path_prefix(path_to_search, user_id, limit, state_filter)

        return {
            "current_category": None,
            "child_categories": [cat.model_dump() for cat in matching_categories],
            "values": all_values,
            "path": path,
            "total_children": len(matching_categories),
            "total_values": len(all_values),
        }
