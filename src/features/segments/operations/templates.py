"""
Create, update, and delete a Segment Template (an ordered aggregate of one or
more saved-Segment rich cards).

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"blocked" (the controller converts that to an HTTP response).
"""
import logging
import sqlite3
from datetime import datetime

from src.features.segments.dto import SegmentTemplate, SegmentTemplateRequest
from src.features.segments.hooks import SEGMENT_HOOKS
from src.features.segments.operations.reads import get_template
from src.features.segments.repository import SegmentTemplateRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)


def _unique_error(exc: sqlite3.IntegrityError, message: str) -> ValueError:
    if "UNIQUE" in str(exc).upper():
        return ValueError(message)
    return ValueError(f"Database constraint failed: {exc}")


def create_template(
    template_repository: SegmentTemplateRepository,
    plugin_registry: PluginRegistry,
    request: SegmentTemplateRequest,
    user_id: str,
) -> SegmentTemplate:
    hook_data, blocked = execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.before_create_template,
        {
            "name": request.name,
            "description": request.description,
            "tags": request.tags,
            "segments": [segment.model_dump(mode="json") for segment in request.segments],
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Segment Template creation blocked")
        logger.warning("Segment-domain operation blocked by plugin: %s", reason)
        raise ValueError(reason)

    if template_repository.get_by_name(request.name, user_id):
        raise ValueError("Segment Template with this name already exists")

    template = SegmentTemplate(
        id=generate_ulid(),
        user_id=user_id,
        name=request.name,
        description=request.description,
        tags=request.tags,
        segments=request.segments,
    )
    try:
        created = template_repository.create(template)
    except sqlite3.IntegrityError as exc:
        raise _unique_error(exc, "Segment Template with this name already exists") from exc

    execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.after_create_template,
        {
            "template_id": created.id,
            "name": created.name,
            "segment_count": len(created.segments),
            "user_id": user_id,
        },
    )
    return created


def update_template(
    template_repository: SegmentTemplateRepository,
    plugin_registry: PluginRegistry,
    template_id: str,
    request: SegmentTemplateRequest,
    user_id: str,
) -> SegmentTemplate:
    existing = get_template(template_repository, template_id, user_id)
    hook_data, blocked = execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.before_update_template,
        {
            "template_id": template_id,
            "old_name": existing.name,
            "new_name": request.name,
            "description": request.description,
            "tags": request.tags,
            "segments": [segment.model_dump(mode="json") for segment in request.segments],
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Segment Template update blocked")
        logger.warning("Segment-domain operation blocked by plugin: %s", reason)
        raise ValueError(reason)

    duplicate = template_repository.get_by_name(request.name, user_id)
    if duplicate and duplicate.id != template_id:
        raise ValueError("Segment Template with this name already exists")

    template = SegmentTemplate(
        id=template_id,
        user_id=user_id,
        name=request.name,
        description=request.description,
        tags=request.tags,
        segments=request.segments,
        created_at=existing.created_at,
        updated_at=datetime.now(),
    )
    try:
        updated = template_repository.update(template_id, template, user_id)
    except sqlite3.IntegrityError as exc:
        raise _unique_error(exc, "Segment Template with this name already exists") from exc
    if not updated:
        raise ValueError("Segment Template not found")

    execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.after_update_template,
        {
            "template_id": updated.id,
            "name": updated.name,
            "segment_count": len(updated.segments),
            "user_id": user_id,
        },
    )
    return updated


def delete_template(
    template_repository: SegmentTemplateRepository,
    plugin_registry: PluginRegistry,
    template_id: str,
    user_id: str,
) -> bool:
    existing = get_template(template_repository, template_id, user_id)
    hook_data, blocked = execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.before_delete_template,
        {"template_id": template_id, "name": existing.name, "user_id": user_id},
    )
    if blocked:
        reason = hook_data.get("block_reason", "Segment Template deletion blocked")
        logger.warning("Segment-domain operation blocked by plugin: %s", reason)
        raise ValueError(reason)

    if not template_repository.delete(template_id, user_id):
        raise ValueError("Segment Template not found")

    execute_hook(
        plugin_registry,
        SEGMENT_HOOKS.after_delete_template,
        {"template_id": template_id, "name": existing.name, "user_id": user_id},
    )
    return True
