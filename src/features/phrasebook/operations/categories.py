"""
Create, update, delete, and toggle a phrasebook Category.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"blocked" (the controller converts that to an HTTP response).
"""
import logging
from datetime import datetime

from src.features.phrasebook.dto import PhrasebookCategory, PhrasebookCategoryRequest
from src.features.phrasebook.hooks import PHRASEBOOK_HOOKS
from src.features.phrasebook.operations.reads import get_category
from src.features.phrasebook.repository import PhrasebookCategoryRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)


def create_category(
    category_repository: PhrasebookCategoryRepository,
    plugin_registry: PluginRegistry,
    request: PhrasebookCategoryRequest,
    user_id: str,
) -> PhrasebookCategory:
    hook_data, blocked = execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.before_create,
        {
            "type": "category",
            "name": request.name,
            "path": request.path,
            "parent_id": request.parent_id,
            "description": request.description,
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Category creation blocked")
        logger.warning(f"Category creation blocked by plugin: {reason}")
        raise ValueError(reason)

    existing = category_repository.get_by_path(request.path, user_id)
    if existing:
        raise ValueError("Category with this path already exists")

    category = PhrasebookCategory(
        id=generate_ulid(),
        name=request.name,
        path=request.path,
        parent_id=request.parent_id,
        description=request.description,
        user_id=user_id,
    )

    success = category_repository.create(category)
    if not success:
        raise ValueError("Failed to create category")

    execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.after_create,
        {
            "type": "category",
            "category_id": category.id,
            "name": category.name,
            "path": category.path,
            "user_id": user_id,
        },
    )

    logger.info(f"Category created: {category.path} (id: {category.id})")
    return category


def update_category(
    category_repository: PhrasebookCategoryRepository,
    plugin_registry: PluginRegistry,
    category_id: str,
    request: PhrasebookCategoryRequest,
    user_id: str,
) -> PhrasebookCategory:
    existing = get_category(category_repository, category_id, user_id)

    hook_data, blocked = execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.before_update,
        {
            "type": "category",
            "category_id": category_id,
            "old_name": existing.name,
            "old_path": existing.path,
            "new_name": request.name,
            "new_path": request.path,
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Category update blocked")
        logger.warning(f"Category update blocked by plugin: {reason}")
        raise ValueError(reason)

    if request.path != existing.path:
        conflicting = category_repository.get_by_path(request.path, user_id)
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
        updated_at=datetime.now(),
    )

    success = category_repository.update(category_id, category)
    if not success:
        raise ValueError("Failed to update category")

    execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.after_update,
        {
            "type": "category",
            "category_id": category_id,
            "name": category.name,
            "path": category.path,
            "user_id": user_id,
        },
    )

    logger.info(f"Category updated: {category.path} (id: {category_id})")
    return category


def delete_category(
    category_repository: PhrasebookCategoryRepository,
    plugin_registry: PluginRegistry,
    category_id: str,
    user_id: str,
) -> bool:
    existing = get_category(category_repository, category_id, user_id)

    hook_data, blocked = execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.before_delete,
        {
            "type": "category",
            "category_id": category_id,
            "name": existing.name,
            "path": existing.path,
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Category deletion blocked")
        logger.warning(f"Category deletion blocked by plugin: {reason}")
        raise ValueError(reason)

    success = category_repository.delete(category_id, user_id)
    if not success:
        raise ValueError("Failed to delete category")

    execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.after_delete,
        {
            "type": "category",
            "category_id": category_id,
            "name": existing.name,
            "path": existing.path,
            "user_id": user_id,
        },
    )

    logger.info(f"Category deleted: {existing.path} (id: {category_id})")
    return True


def toggle_category_active(
    category_repository: PhrasebookCategoryRepository,
    category_id: str,
    is_active: bool,
    user_id: str,
) -> PhrasebookCategory:
    existing = get_category(category_repository, category_id, user_id)

    success = category_repository.update_active_state(category_id, user_id, is_active)
    if not success:
        raise ValueError("Failed to update category active state")

    updated = category_repository.get_by_id(category_id, user_id)
    state_str = "activated" if is_active else "deactivated"
    logger.info(f"Category {state_str}: {existing.path} (id: {category_id})")
    return updated
