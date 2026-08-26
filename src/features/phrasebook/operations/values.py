"""
Create, update, delete, toggle, and attach a preview image to a phrasebook
Value.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"blocked" (the controller converts that to an HTTP response).
"""
import logging
from datetime import datetime
from typing import Optional

from src.features.phrasebook.dto import PhrasebookValue, PhrasebookValueRequest
from src.features.phrasebook.hooks import PHRASEBOOK_HOOKS
from src.features.phrasebook.operations.reads import get_category, get_value
from src.features.phrasebook.repository import (
    PhrasebookCategoryRepository,
    PhrasebookValueRepository,
)
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)


def create_value(
    value_repository: PhrasebookValueRepository,
    category_repository: PhrasebookCategoryRepository,
    plugin_registry: PluginRegistry,
    request: PhrasebookValueRequest,
    user_id: str,
) -> PhrasebookValue:
    # Validate category exists
    get_category(category_repository, request.category_id, user_id)

    hook_data, blocked = execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.before_create,
        {
            "type": "value",
            "category_id": request.category_id,
            "label": request.label,
            "value": request.value,
            "user_id": user_id,
        },
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
        user_id=user_id,
    )

    success = value_repository.create(value)
    if not success:
        raise ValueError("Failed to create value")

    execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.after_create,
        {
            "type": "value",
            "value_id": value.id,
            "category_id": value.category_id,
            "label": value.label,
            "user_id": user_id,
        },
    )

    logger.info(f"Value created: {value.label} (id: {value.id})")
    return value


def update_value(
    value_repository: PhrasebookValueRepository,
    category_repository: PhrasebookCategoryRepository,
    plugin_registry: PluginRegistry,
    value_id: str,
    request: PhrasebookValueRequest,
    user_id: str,
) -> PhrasebookValue:
    existing = get_value(value_repository, value_id, user_id)

    # Validate category exists
    get_category(category_repository, request.category_id, user_id)

    hook_data, blocked = execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.before_update,
        {
            "type": "value",
            "value_id": value_id,
            "old_label": existing.label,
            "new_label": request.label,
            "old_value": existing.value,
            "new_value": request.value,
            "user_id": user_id,
        },
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
        updated_at=datetime.now(),
    )

    success = value_repository.update(value_id, value)
    if not success:
        raise ValueError("Failed to update value")

    execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.after_update,
        {
            "type": "value",
            "value_id": value_id,
            "label": value.label,
            "user_id": user_id,
        },
    )

    logger.info(f"Value updated: {value.label} (id: {value_id})")
    return value


def delete_value(
    value_repository: PhrasebookValueRepository,
    plugin_registry: PluginRegistry,
    value_id: str,
    user_id: str,
) -> bool:
    existing = get_value(value_repository, value_id, user_id)

    hook_data, blocked = execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.before_delete,
        {
            "type": "value",
            "value_id": value_id,
            "label": existing.label,
            "category_id": existing.category_id,
            "user_id": user_id,
        },
    )
    if blocked:
        reason = hook_data.get("block_reason", "Value deletion blocked")
        logger.warning(f"Value deletion blocked by plugin: {reason}")
        raise ValueError(reason)

    success = value_repository.delete(value_id, user_id)
    if not success:
        raise ValueError("Failed to delete value")

    execute_hook(
        plugin_registry,
        PHRASEBOOK_HOOKS.after_delete,
        {
            "type": "value",
            "value_id": value_id,
            "label": existing.label,
            "user_id": user_id,
        },
    )

    logger.info(f"Value deleted: {existing.label} (id: {value_id})")
    return True


def toggle_value_active(
    value_repository: PhrasebookValueRepository,
    value_id: str,
    is_active: bool,
    user_id: str,
) -> PhrasebookValue:
    existing = get_value(value_repository, value_id, user_id)

    success = value_repository.update_active_state(value_id, user_id, is_active)
    if not success:
        raise ValueError("Failed to update value active state")

    updated = value_repository.get_by_id(value_id, user_id)
    state_str = "activated" if is_active else "deactivated"
    logger.info(f"Value {state_str}: {existing.label} (id: {value_id})")
    return updated


def attach_preview_image(
    value_repository: PhrasebookValueRepository,
    value_id: str,
    user_id: str,
    file_id: Optional[str],
    generation_id: Optional[str],
) -> PhrasebookValue:
    existing = get_value(value_repository, value_id, user_id)

    success = value_repository.update_preview_file(value_id, user_id, file_id, generation_id)
    if not success:
        raise ValueError("Failed to update value preview image")

    updated = value_repository.get_by_id(value_id, user_id)
    logger.info(f"Preview image attached to value: {existing.label} (id: {value_id})")
    return updated
