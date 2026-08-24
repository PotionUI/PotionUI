"""
Phrasebook domain manager.

Handles all business logic for phrasebook categories and values.
Framework-agnostic - uses ValueError for errors (controller converts to HTTP responses).
"""
import logging
from typing import Optional, Dict, Any
from datetime import datetime


from src.platform.util.ids import generate_ulid
from src.features.phrasebook.dto import (
    PhrasebookCategory,
    PhrasebookValue,
    PhrasebookCategoryRequest,
    PhrasebookValueRequest,
    PhrasebookStateFilter,
)
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.phrasebook.hooks import PHRASEBOOK_HOOKS

logger = logging.getLogger(__name__)


class PhrasebookManager:
    """
    Coordinates phrasebook operations.

    Handles CRUD for categories and values, search functionality,
    and plugin hook execution.
    """

    def __init__(
        self,
        category_repository: PhrasebookCategoryRepository,
        value_repository: PhrasebookValueRepository,
        plugin_registry: PluginRegistry
    ):
        self.categories = category_repository
        self.values = value_repository
        self.plugins = plugin_registry

    # ========== Category Operations ==========

    def get_category_by_id(self, category_id: str, user_id: str) -> PhrasebookCategory:
        """
        Get a specific category by ID.

        Raises:
            ValueError: If category not found
        """
        category = self.categories.get_by_id(category_id, user_id)
        if not category:
            raise ValueError("Category not found")
        return category

    def create_category(self, request: PhrasebookCategoryRequest, user_id: str) -> PhrasebookCategory:
        """
        Create a new category.

        Executes hooks:
        - phrasebook.before_create: Can modify/validate data or block
        - phrasebook.after_create: Notification of successful creation

        Raises:
            ValueError: If path already exists or creation blocked
        """
        # Execute before_create hook
        hook_data, blocked = execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.before_create,
            {
                "type": "category",
                "name": request.name,
                "path": request.path,
                "parent_id": request.parent_id,
                "description": request.description,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Category creation blocked")
            logger.warning(f"Category creation blocked by plugin: {reason}")
            raise ValueError(reason)

        # Check for duplicate paths
        existing = self.categories.get_by_path(request.path, user_id)
        if existing:
            raise ValueError("Category with this path already exists")

        category = PhrasebookCategory(
            id=generate_ulid(),
            name=request.name,
            path=request.path,
            parent_id=request.parent_id,
            description=request.description,
            user_id=user_id
        )

        success = self.categories.create(category)
        if not success:
            raise ValueError("Failed to create category")

        # Execute after_create hook
        execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.after_create,
            {
                "type": "category",
                "category_id": category.id,
                "name": category.name,
                "path": category.path,
                "user_id": user_id
            }
        )

        logger.info(f"Category created: {category.path} (id: {category.id})")
        return category

    def update_category(
        self, category_id: str, request: PhrasebookCategoryRequest, user_id: str
    ) -> PhrasebookCategory:
        """
        Update an existing category.

        Executes hooks:
        - phrasebook.before_update: Can modify/validate data or block
        - phrasebook.after_update: Notification of successful update

        Raises:
            ValueError: If category not found, path conflicts, or update blocked
        """
        existing = self.categories.get_by_id(category_id, user_id)
        if not existing:
            raise ValueError("Category not found")

        # Execute before_update hook
        hook_data, blocked = execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.before_update,
            {
                "type": "category",
                "category_id": category_id,
                "old_name": existing.name,
                "old_path": existing.path,
                "new_name": request.name,
                "new_path": request.path,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Category update blocked")
            logger.warning(f"Category update blocked by plugin: {reason}")
            raise ValueError(reason)

        # Check if new path conflicts with another category
        if request.path != existing.path:
            conflicting = self.categories.get_by_path(request.path, user_id)
            if conflicting and conflicting.id != category_id:
                raise ValueError("Category with this path already exists")

        category = PhrasebookCategory(
            id=category_id,
            name=request.name,
            path=request.path,
            parent_id=request.parent_id,
            description=request.description,
            user_id=user_id,
            created_at=existing.created_at,
            updated_at=datetime.now()
        )

        success = self.categories.update(category_id, category)
        if not success:
            raise ValueError("Failed to update category")

        # Execute after_update hook
        execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.after_update,
            {
                "type": "category",
                "category_id": category_id,
                "name": category.name,
                "path": category.path,
                "user_id": user_id
            }
        )

        logger.info(f"Category updated: {category.path} (id: {category_id})")
        return category

    def delete_category(self, category_id: str, user_id: str) -> bool:
        """
        Delete a category and all its values.

        Executes hooks:
        - phrasebook.before_delete: Can block deletion
        - phrasebook.after_delete: Notification of successful deletion

        Raises:
            ValueError: If category not found or deletion blocked
        """
        existing = self.categories.get_by_id(category_id, user_id)
        if not existing:
            raise ValueError("Category not found")

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.before_delete,
            {
                "type": "category",
                "category_id": category_id,
                "name": existing.name,
                "path": existing.path,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Category deletion blocked")
            logger.warning(f"Category deletion blocked by plugin: {reason}")
            raise ValueError(reason)

        success = self.categories.delete(category_id, user_id)
        if not success:
            raise ValueError("Failed to delete category")

        # Execute after_delete hook
        execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.after_delete,
            {
                "type": "category",
                "category_id": category_id,
                "name": existing.name,
                "path": existing.path,
                "user_id": user_id
            }
        )

        logger.info(f"Category deleted: {existing.path} (id: {category_id})")
        return True

    def toggle_category_active(self, category_id: str, is_active: bool, user_id: str) -> PhrasebookCategory:
        """
        Toggle the active state of a category.

        Args:
            category_id: The category ID
            is_active: New active state
            user_id: The user ID

        Raises:
            ValueError: If category not found

        Returns:
            The updated category
        """
        existing = self.categories.get_by_id(category_id, user_id)
        if not existing:
            raise ValueError("Category not found")

        success = self.categories.update_active_state(category_id, user_id, is_active)
        if not success:
            raise ValueError("Failed to update category active state")

        # Fetch and return the updated category
        updated = self.categories.get_by_id(category_id, user_id)
        state_str = "activated" if is_active else "deactivated"
        logger.info(f"Category {state_str}: {existing.path} (id: {category_id})")
        return updated

    # ========== Value Operations ==========

    def get_value_by_id(self, value_id: str, user_id: str) -> PhrasebookValue:
        """
        Get a specific value by ID.

        Raises:
            ValueError: If value not found
        """
        value = self.values.get_by_id(value_id, user_id)

        if not value:
            raise ValueError("Value not found")
        return value

    def create_value(self, request: PhrasebookValueRequest, user_id: str) -> PhrasebookValue:
        """
        Create a new value.

        Executes hooks:
        - phrasebook.before_create: Can modify/validate data or block
        - phrasebook.after_create: Notification of successful creation

        Raises:
            ValueError: If category not found or creation blocked
        """
        # Validate category exists
        if not self.categories.get_by_id(request.category_id, user_id):
            raise ValueError("Category not found")

        # Execute before_create hook
        hook_data, blocked = execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.before_create,
            {
                "type": "value",
                "category_id": request.category_id,
                "label": request.label,
                "value": request.value,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Value creation blocked")
            logger.warning(f"Value creation blocked by plugin: {reason}")
            raise ValueError(reason)

        value = PhrasebookValue(
            id=generate_ulid(),
            category_id=request.category_id,
            label=request.label,
            value=request.value,
            sort_order=request.sort_order,
            user_id=user_id
        )

        success = self.values.create(value)
        if not success:
            raise ValueError("Failed to create value")

        # Execute after_create hook
        execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.after_create,
            {
                "type": "value",
                "value_id": value.id,
                "category_id": value.category_id,
                "label": value.label,
                "user_id": user_id
            }
        )

        logger.info(f"Value created: {value.label} (id: {value.id})")
        return value

    def update_value(
        self, value_id: str, request: PhrasebookValueRequest, user_id: str
    ) -> PhrasebookValue:
        """
        Update an existing value.

        Executes hooks:
        - phrasebook.before_update: Can modify/validate data or block
        - phrasebook.after_update: Notification of successful update

        Raises:
            ValueError: If value/category not found or update blocked
        """
        existing = self.get_value_by_id(value_id, user_id)

        # Validate category exists
        if not self.categories.get_by_id(request.category_id, user_id):
            raise ValueError("Category not found")

        # Execute before_update hook
        hook_data, blocked = execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.before_update,
            {
                "type": "value",
                "value_id": value_id,
                "old_label": existing.label,
                "new_label": request.label,
                "old_value": existing.value,
                "new_value": request.value,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Value update blocked")
            logger.warning(f"Value update blocked by plugin: {reason}")
            raise ValueError(reason)

        value = PhrasebookValue(
            id=value_id,
            category_id=request.category_id,
            label=request.label,
            value=request.value,
            sort_order=request.sort_order,
            user_id=user_id,
            created_at=existing.created_at,
            updated_at=datetime.now()
        )

        success = self.values.update(value_id, value)
        if not success:
            raise ValueError("Failed to update value")

        # Execute after_update hook
        execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.after_update,
            {
                "type": "value",
                "value_id": value_id,
                "label": value.label,
                "user_id": user_id
            }
        )

        logger.info(f"Value updated: {value.label} (id: {value_id})")
        return value

    def delete_value(self, value_id: str, user_id: str) -> bool:
        """
        Delete a value.

        Executes hooks:
        - phrasebook.before_delete: Can block deletion
        - phrasebook.after_delete: Notification of successful deletion

        Raises:
            ValueError: If value not found or deletion blocked
        """
        existing = self.get_value_by_id(value_id, user_id)

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.before_delete,
            {
                "type": "value",
                "value_id": value_id,
                "label": existing.label,
                "category_id": existing.category_id,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Value deletion blocked")
            logger.warning(f"Value deletion blocked by plugin: {reason}")
            raise ValueError(reason)

        success = self.values.delete(value_id, user_id)
        if not success:
            raise ValueError("Failed to delete value")

        # Execute after_delete hook
        execute_hook(self.plugins,
            PHRASEBOOK_HOOKS.after_delete,
            {
                "type": "value",
                "value_id": value_id,
                "label": existing.label,
                "user_id": user_id
            }
        )

        logger.info(f"Value deleted: {existing.label} (id: {value_id})")
        return True

    def toggle_value_active(self, value_id: str, is_active: bool, user_id: str) -> PhrasebookValue:
        """
        Toggle the active state of a value.

        Args:
            value_id: The value ID
            is_active: New active state
            user_id: The user ID

        Raises:
            ValueError: If value not found

        Returns:
            The updated value
        """
        existing = self.values.get_by_id(value_id, user_id)
        if not existing:
            raise ValueError("Value not found")

        success = self.values.update_active_state(value_id, user_id, is_active)
        if not success:
            raise ValueError("Failed to update value active state")

        # Fetch and return the updated value
        updated = self.values.get_by_id(value_id, user_id)
        state_str = "activated" if is_active else "deactivated"
        logger.info(f"Value {state_str}: {existing.label} (id: {value_id})")
        return updated

    def attach_preview_image(
        self,
        value_id: str,
        user_id: str,
        file_id: Optional[str],
        generation_id: Optional[str]
    ) -> PhrasebookValue:
        """
        Attach a preview image to a value.

        Args:
            value_id: The value ID
            user_id: The user ID
            file_id: File record ID for the preview image (or None to clear)
            generation_id: The generation ID that created this preview (or None to clear)

        Raises:
            ValueError: If value not found

        Returns:
            The updated value
        """
        existing = self.values.get_by_id(value_id, user_id)
        if not existing:
            raise ValueError("Value not found")

        success = self.values.update_preview_file(value_id, user_id, file_id, generation_id)
        if not success:
            raise ValueError("Failed to update value preview image")

        # Fetch and return the updated value
        updated = self.values.get_by_id(value_id, user_id)
        logger.info(f"Preview image attached to value: {existing.label} (id: {value_id})")
        return updated

    # ========== Search Operations ==========

    def search_phrasebook(
        self,
        path: str,
        user_id: str,
        limit: int = 50,
        state_filter: PhrasebookStateFilter = PhrasebookStateFilter.ACTIVE
    ) -> Dict[str, Any]:
        """
        Search for phrasebook suggestions by path.

        Args:
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
        exact_category = self.categories.get_by_path(path_to_search, user_id) if path_to_search else None

        # For ACTIVE state filter, check if the exact category itself is active
        if exact_category and state_filter == PhrasebookStateFilter.ACTIVE and not exact_category.is_active:
            exact_category = None

        if exact_category:
            # Get direct child categories
            child_categories = self.categories.get_children(exact_category.id, user_id)
            # Filter by state if needed
            if state_filter == PhrasebookStateFilter.ACTIVE:
                child_categories = [c for c in child_categories if c.is_active]
            elif state_filter == PhrasebookStateFilter.INACTIVE:
                child_categories = [c for c in child_categories if not c.is_active]

            # Get values for this exact category with state filtering
            category_values = self.values.get_by_category(exact_category.id, user_id, state_filter)

            return {
                "current_category": exact_category.model_dump(),
                "child_categories": [cat.model_dump() for cat in child_categories],
                "values": [val.model_dump() for val in category_values],
                "path": path,  # Keep original path for display
                "total_children": len(child_categories),
                "total_values": len(category_values)
            }
        else:
            # Path doesn't match exact category, search for prefix matches
            if path_to_search:
                matching_categories = self.categories.search_by_path_prefix(path_to_search, user_id)
            else:
                matching_categories = self.categories.get_children(None, user_id)

            # Filter by state if needed
            if state_filter == PhrasebookStateFilter.ACTIVE:
                matching_categories = [c for c in matching_categories if c.is_active]
            elif state_filter == PhrasebookStateFilter.INACTIVE:
                matching_categories = [c for c in matching_categories if not c.is_active]

            # For prefix search, also get values from matching categories with state filtering
            all_values = []
            if path_to_search:
                all_values = self.values.search_by_path_prefix(path_to_search, user_id, limit, state_filter)

            return {
                "current_category": None,
                "child_categories": [cat.model_dump() for cat in matching_categories],
                "values": all_values,
                "path": path,
                "total_children": len(matching_categories),
                "total_values": len(all_values)
            }
