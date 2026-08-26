"""
Create, update, and delete a saved Segment (one reusable rich card).

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"blocked" (the controller converts that to an HTTP response).
"""
import logging
import sqlite3
from datetime import datetime

from src.features.segments.dto import SavedSegment, SavedSegmentRequest
from src.features.segments.hooks import SEGMENT_HOOKS
from src.features.segments.operations.reads import get_category, get_segment
from src.features.segments.repository import SavedSegmentRepository, SegmentCategoryRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)


def _unique_error(exc: sqlite3.IntegrityError, message: str) -> ValueError:
    if "UNIQUE" in str(exc).upper():
        return ValueError(message)
    return ValueError(f"Database constraint failed: {exc}")


def create_segment(
    segment_repository: SavedSegmentRepository,
    category_repository: SegmentCategoryRepository,
    plugin_registry: PluginRegistry,
    request: SavedSegmentRequest,
    user_id: str,
) -> SavedSegment:
    category = get_category(category_repository, request.category_id, user_id)
    hook_data, blocked = execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.before_create_segment,
        {
            "name": request.name,
            "category_id": request.category_id,
            "segment": request.model_dump(mode="json"),
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Saved Segment creation blocked")
        logger.warning("Segment-domain operation blocked by plugin: %s", reason)
        raise ValueError(reason)

    if segment_repository.get_by_name(request.name, user_id):
        raise ValueError("Saved Segment with this name already exists")

    segment = SavedSegment(
        id=generate_ulid(),
        user_id=user_id,
        name=request.name,
        category_id=request.category_id,
        type=request.type,
        content=request.content,
        chips=request.chips,
        enabled=request.enabled,
        color=request.color,
        effective_color=request.color or category.color,
        description=request.description,
        tags=request.tags,
    )
    try:
        created = segment_repository.create(segment)
    except sqlite3.IntegrityError as exc:
        raise _unique_error(exc, "Saved Segment with this name already exists") from exc
    if not created:
        raise ValueError("Category not found")

    execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.after_create_segment,
        {
            "segment_id": created.id,
            "name": created.name,
            "category_id": created.category_id,
            "user_id": user_id,
        },
    )
    return created


def update_segment(
    segment_repository: SavedSegmentRepository,
    category_repository: SegmentCategoryRepository,
    plugin_registry: PluginRegistry,
    segment_id: str,
    request: SavedSegmentRequest,
    user_id: str,
) -> SavedSegment:
    existing = get_segment(segment_repository, segment_id, user_id)
    category = get_category(category_repository, request.category_id, user_id)
    hook_data, blocked = execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.before_update_segment,
        {
            "segment_id": segment_id,
            "old_name": existing.name,
            "new_name": request.name,
            "category_id": request.category_id,
            "segment": request.model_dump(mode="json"),
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Saved Segment update blocked")
        logger.warning("Segment-domain operation blocked by plugin: %s", reason)
        raise ValueError(reason)

    duplicate = segment_repository.get_by_name(request.name, user_id)
    if duplicate and duplicate.id != segment_id:
        raise ValueError("Saved Segment with this name already exists")

    segment = SavedSegment(
        id=segment_id,
        user_id=user_id,
        name=request.name,
        category_id=request.category_id,
        type=request.type,
        content=request.content,
        chips=request.chips,
        enabled=request.enabled,
        color=request.color,
        effective_color=request.color or category.color,
        description=request.description,
        tags=request.tags,
        created_at=existing.created_at,
        updated_at=datetime.now(),
    )
    try:
        updated = segment_repository.update(segment_id, segment, user_id)
    except sqlite3.IntegrityError as exc:
        raise _unique_error(exc, "Saved Segment with this name already exists") from exc
    if not updated:
        raise ValueError("Saved Segment not found")

    execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.after_update_segment,
        {
            "segment_id": updated.id,
            "name": updated.name,
            "category_id": updated.category_id,
            "user_id": user_id,
        },
    )
    return updated


def delete_segment(
    segment_repository: SavedSegmentRepository,
    plugin_registry: PluginRegistry,
    segment_id: str,
    user_id: str,
) -> bool:
    existing = get_segment(segment_repository, segment_id, user_id)
    hook_data, blocked = execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.before_delete_segment,
        {
            "segment_id": segment_id,
            "name": existing.name,
            "category_id": existing.category_id,
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Saved Segment deletion blocked")
        logger.warning("Segment-domain operation blocked by plugin: %s", reason)
        raise ValueError(reason)

    if not segment_repository.delete(segment_id, user_id):
        raise ValueError("Saved Segment not found")

    execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.after_delete_segment,
        {"segment_id": segment_id, "name": existing.name, "user_id": user_id},
    )
    return True
