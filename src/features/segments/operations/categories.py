"""
Create, update, and delete a Segment Category.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"blocked" (the controller converts that to an HTTP response).
"""
import logging
import sqlite3
from datetime import datetime

from src.features.segments.dto import SegmentCategory, SegmentCategoryRequest
from src.features.segments.hooks import SEGMENT_HOOKS
from src.features.segments.operations.reads import get_category
from src.features.segments.repository import SegmentCategoryRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)


def _unique_error(exc: sqlite3.IntegrityError, message: str) -> ValueError:
    if "UNIQUE" in str(exc).upper():
        return ValueError(message)
    return ValueError(f"Database constraint failed: {exc}")


def create_category(
    category_repository: SegmentCategoryRepository,
    plugin_registry: PluginRegistry,
    request: SegmentCategoryRequest,
    user_id: str,
) -> SegmentCategory:
    hook_data, blocked = execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.before_create_category,
        {
            "name": request.name,
            "description": request.description,
            "color": request.color,
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Category creation blocked")
        logger.warning("Segment-domain operation blocked by plugin: %s", reason)
        raise ValueError(reason)

    if category_repository.get_by_name(request.name, user_id):
        raise ValueError("Category with this name already exists")

    category = SegmentCategory(
        id=generate_ulid(),
        user_id=user_id,
        name=request.name,
        description=request.description,
        color=request.color,
    )
    try:
        created = category_repository.create(category)
    except sqlite3.IntegrityError as exc:
        raise _unique_error(exc, "Category with this name already exists") from exc

    execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.after_create_category,
        {"category_id": created.id, "name": created.name, "user_id": user_id},
    )
    return created


def update_category(
    category_repository: SegmentCategoryRepository,
    plugin_registry: PluginRegistry,
    category_id: str,
    request: SegmentCategoryRequest,
    user_id: str,
) -> SegmentCategory:
    existing = get_category(category_repository, category_id, user_id)
    hook_data, blocked = execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.before_update_category,
        {
            "category_id": category_id,
            "old_name": existing.name,
            "new_name": request.name,
            "description": request.description,
            "color": request.color,
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Category update blocked")
        logger.warning("Segment-domain operation blocked by plugin: %s", reason)
        raise ValueError(reason)

    duplicate = category_repository.get_by_name(request.name, user_id)
    if duplicate and duplicate.id != category_id:
        raise ValueError("Category with this name already exists")

    category = SegmentCategory(
        id=category_id,
        user_id=user_id,
        name=request.name,
        description=request.description,
        color=request.color,
        created_at=existing.created_at,
        updated_at=datetime.now(),
    )
    try:
        updated = category_repository.update(category_id, category, user_id)
    except sqlite3.IntegrityError as exc:
        raise _unique_error(exc, "Category with this name already exists") from exc
    if not updated:
        raise ValueError("Category not found")

    execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.after_update_category,
        {"category_id": updated.id, "name": updated.name, "user_id": user_id},
    )
    return updated


def delete_category(
    category_repository: SegmentCategoryRepository,
    plugin_registry: PluginRegistry,
    category_id: str,
    user_id: str,
) -> bool:
    existing = get_category(category_repository, category_id, user_id)
    hook_data, blocked = execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.before_delete_category,
        {"category_id": category_id, "name": existing.name, "user_id": user_id},
    )
    if blocked:
        reason = hook_data.get("block_reason", "Category deletion blocked")
        logger.warning("Segment-domain operation blocked by plugin: %s", reason)
        raise ValueError(reason)

    if category_repository.has_saved_segments(category_id, user_id):
        raise ValueError("Cannot delete category with existing saved segments")
    try:
        deleted = category_repository.delete(category_id, user_id)
    except sqlite3.IntegrityError as exc:
        raise ValueError("Cannot delete category with existing saved segments") from exc
    if not deleted:
        raise ValueError("Category not found")

    execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.after_delete_category,
        {"category_id": category_id, "name": existing.name, "user_id": user_id},
    )
    return True
