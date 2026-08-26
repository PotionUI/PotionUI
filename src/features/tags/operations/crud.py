"""
Create, update and delete a tag.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"blocked" (the controller converts that to an HTTP response).
"""
import logging

from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.repository import DatabasePresetRepository
from src.features.tags.dto import Tag, TagType, CreateTagRequest, UpdateTagRequest, USER_SCOPED_TAG_TYPES, effective_user_id_for_type
from src.features.tags.errors import TagInUseByPresetError
from src.features.tags.repository import TagRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.tags.hooks import TAG_HOOKS

logger = logging.getLogger(__name__)


def create_tag(tag_repository: TagRepository, plugin_registry: PluginRegistry, request: CreateTagRequest, user_id: str, is_admin: bool = False) -> Tag:
    """
    Create a new tag.

    Executes hooks:
    - tag.before_create: Can modify/validate data or block
    - tag.after_create: Notification of successful creation

    Args:
        is_admin: Whether the caller is an administrator (MODEL tags are
            global and may only be authored by admins)

    Raises:
        ValueError: If invalid type or creation blocked
    """
    if request.type not in [TagType.MODEL, *USER_SCOPED_TAG_TYPES]:
        raise ValueError("Invalid tag type. Must be MODEL, GENERATION or UPLOAD")

    # MODEL tags are global (shared by every user); only an admin may author them.
    if request.type == TagType.MODEL and not is_admin:
        raise ValueError("Tag not found or access denied")

    effective_user_id = effective_user_id_for_type(request.type, user_id)

    hook_data, blocked = execute_hook(
        plugin_registry,
        TAG_HOOKS.before_create,
        {"name": request.name, "type": request.type.value, "user_id": effective_user_id},
    )

    if blocked:
        reason = hook_data.get("block_reason", "Tag creation blocked")
        logger.warning(f"Tag creation blocked by plugin: {reason}")
        raise ValueError(reason)

    # Allow hooks to modify name
    name = hook_data.get("name", request.name)

    tag = tag_repository.create_tag(name=name, type=request.type.value, user_id=effective_user_id)
    if not tag:
        raise ValueError("Failed to create tag")

    execute_hook(
        plugin_registry,
        TAG_HOOKS.after_create,
        {"tag_id": tag.id, "name": tag.name, "type": tag.type, "user_id": tag.user_id},
    )

    logger.info(f"Tag created: {tag.name} (id: {tag.id}, type: {tag.type})")
    return tag


def update_tag(tag_repository: TagRepository, plugin_registry: PluginRegistry, tag_id: str, request: UpdateTagRequest, user_id: str, is_admin: bool = False) -> Tag:
    """
    Update a tag's name.

    Executes hooks:
    - tag.before_update: Can modify/validate data or block
    - tag.after_update: Notification of successful update

    Args:
        is_admin: Whether the caller is an administrator (MODEL tags are
            global and may only be edited by admins)

    Raises:
        ValueError: If tag not found, access denied, or update blocked
    """
    existing = tag_repository.get_tag_by_id(tag_id)
    if not existing:
        raise ValueError("Tag not found or access denied")

    if existing.type in USER_SCOPED_TAG_TYPES and existing.user_id != user_id:
        raise ValueError("Tag not found or access denied")

    # MODEL tags are global; only an admin may edit them.
    if existing.type == TagType.MODEL and not is_admin:
        raise ValueError("Tag not found or access denied")

    hook_data, blocked = execute_hook(
        plugin_registry,
        TAG_HOOKS.before_update,
        {
            "tag_id": tag_id,
            "old_name": existing.name,
            "new_name": request.name,
            "type": existing.type,
            "user_id": existing.user_id,
        },
    )

    if blocked:
        reason = hook_data.get("block_reason", "Tag update blocked")
        logger.warning(f"Tag update blocked by plugin: {reason}")
        raise ValueError(reason)

    name = hook_data.get("new_name", request.name)

    success = tag_repository.update_tag(tag_id, name)
    if not success:
        raise ValueError("Failed to update tag")

    updated_tag = tag_repository.get_tag_by_id(tag_id)
    if not updated_tag:
        raise ValueError("Failed to retrieve updated tag")

    execute_hook(
        plugin_registry,
        TAG_HOOKS.after_update,
        {"tag_id": tag_id, "name": updated_tag.name, "type": updated_tag.type, "user_id": updated_tag.user_id},
    )

    logger.info(f"Tag updated: {updated_tag.name} (id: {tag_id})")
    return updated_tag


def delete_tag(
    tag_repository: TagRepository,
    plugin_registry: PluginRegistry,
    database_preset_repository: DatabasePresetRepository,
    file_preset_repository: FilePresetRepository,
    tag_id: str,
    user_id: str,
    is_admin: bool = False,
) -> bool:
    """
    Delete a tag.

    Executes hooks:
    - tag.before_delete: Can block deletion
    - tag.after_delete: Notification of successful deletion

    Args:
        is_admin: Whether the caller is an administrator (MODEL tags are
            global and may only be deleted by admins)

    Raises:
        ValueError: If tag not found, access denied, or deletion blocked
        TagInUseByPresetError: If an installed preset's stored configuration
            references this tag.

    Returns:
        True if deleted successfully
    """
    existing = tag_repository.get_tag_by_id(tag_id)
    if not existing:
        raise ValueError("Tag not found or access denied")

    if existing.type in USER_SCOPED_TAG_TYPES and existing.user_id != user_id:
        raise ValueError("Tag not found or access denied")

    # MODEL tags are global; only an admin may delete them.
    if existing.type == TagType.MODEL and not is_admin:
        raise ValueError("Tag not found or access denied")

    # Refuse to delete a tag an admin has wired into a preset's stored
    # configuration (e.g. `model_tags`) - no force flag, unset it from the
    # preset's configuration first. See docs/presets.md "Configuration (admin-set)".
    used_by_refs = database_preset_repository.find_presets_referencing_tag(tag_id)
    if used_by_refs:
        used_by = []
        for ref in used_by_refs:
            preset_template = file_preset_repository.find_preset_by_id(ref["preset_id"])
            used_by.append({
                "preset_id": ref["preset_id"],
                "preset_name": preset_template.name if preset_template else ref["preset_id"],
                "key": ref["key"],
            })
        raise TagInUseByPresetError(tag_id, used_by)

    hook_data, blocked = execute_hook(
        plugin_registry,
        TAG_HOOKS.before_delete,
        {"tag_id": tag_id, "name": existing.name, "type": existing.type, "user_id": existing.user_id},
    )

    if blocked:
        reason = hook_data.get("block_reason", "Tag deletion blocked")
        logger.warning(f"Tag deletion blocked by plugin: {reason}")
        raise ValueError(reason)

    success = tag_repository.delete_tag(tag_id)
    if not success:
        raise ValueError("Failed to delete tag")

    execute_hook(
        plugin_registry,
        TAG_HOOKS.after_delete,
        {"tag_id": tag_id, "name": existing.name, "type": existing.type, "user_id": existing.user_id},
    )

    logger.info(f"Tag deleted: {existing.name} (id: {tag_id})")
    return True
