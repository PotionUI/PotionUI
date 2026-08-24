"""
Tag domain manager.

Handles all business logic for tags. Framework-agnostic - uses ValueError
for errors (controller converts to HTTP responses).
"""
import logging
from typing import List, Optional


from src.features.tags.dto import Tag, TagWithCount, TagType, CreateTagRequest, UpdateTagRequest
from src.features.tags.repository import TagRepository
from src.features.presets.repository import DatabasePresetRepository
from src.features.presets.file_repository import FilePresetRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.tags.hooks import TAG_HOOKS

logger = logging.getLogger(__name__)


class TagInUseByPresetError(ValueError):
    """Raised by delete_tag when the tag is referenced by an installed preset's
    stored `configuration:` values (e.g. a `model_tags` entry). No force flag -
    the admin must unset it from the preset's configuration first. See
    docs/presets.md "Configuration (admin-set)"."""

    def __init__(self, tag_id: str, used_by: List[dict]):
        self.tag_id = tag_id
        self.used_by = used_by
        super().__init__(f"Tag '{tag_id}' is used by {len(used_by)} preset configuration(s)")


class TagManager:
    """
    Coordinates tag operations.

    Handles CRUD for tags, search functionality, and plugin hook execution.
    MODEL tags are global (no user_id), GENERATION tags are user-specific.
    """

    def __init__(
        self,
        tag_repository: TagRepository,
        plugin_registry: PluginRegistry,
        database_preset_repository: DatabasePresetRepository,
        file_preset_repository: FilePresetRepository
    ):
        self.repository = tag_repository
        self.plugins = plugin_registry
        self.preset_repository = database_preset_repository
        self.file_preset_repository = file_preset_repository

    # Every type except MODEL is owned by the user who created it. MODEL tags are
    # global (user_id=None) and admin-authored.
    _USER_SCOPED_TYPES = (TagType.GENERATION, TagType.UPLOAD)

    def _get_user_id_for_type(self, tag_type: TagType, user_id: str) -> Optional[str]:
        """
        Determine the user_id to use based on tag type.

        MODEL tags are global (user_id=None), GENERATION and UPLOAD tags are
        user-specific.
        """
        return None if tag_type == TagType.MODEL else user_id

    # ========== Read Operations ==========

    def get_tags(self, tag_type: TagType, user_id: str) -> List[TagWithCount]:
        """
        Get all tags of specified type with usage counts.

        Args:
            tag_type: The type of tags to retrieve
            user_id: The current user's ID (used only for GENERATION tags)

        Returns:
            List of tags with their usage counts
        """
        effective_user_id = self._get_user_id_for_type(tag_type, user_id)
        return self.repository.get_tags_with_counts(type=tag_type.value, user_id=effective_user_id)

    def get_tag_by_id(self, tag_id: str, user_id: str) -> Tag:
        """
        Get a specific tag by ID.

        Args:
            tag_id: The tag ID
            user_id: The current user's ID (for ownership verification)

        Raises:
            ValueError: If tag not found or access denied

        Returns:
            The tag
        """
        tag = self.repository.get_tag_by_id(tag_id)
        if not tag:
            raise ValueError("Tag not found")

        # For user-owned tag types, verify ownership
        if tag.type in self._USER_SCOPED_TYPES and tag.user_id != user_id:
            raise ValueError("Tag not found or access denied")

        return tag

    def search_tags(
        self,
        query: str,
        tag_type: TagType,
        user_id: str,
        limit: int = 10
    ) -> List[Tag]:
        """
        Search tags by name.

        Args:
            query: Search query string
            tag_type: Type of tags to search
            user_id: The current user's ID
            limit: Maximum number of results

        Returns:
            List of matching tags
        """
        effective_user_id = self._get_user_id_for_type(tag_type, user_id)
        return self.repository.search_tags(
            query=query,
            type=tag_type.value,
            user_id=effective_user_id,
            limit=limit
        )

    # ========== Create Operations ==========

    def create_tag(self, request: CreateTagRequest, user_id: str, is_admin: bool = False) -> Tag:
        """
        Create a new tag.

        Executes hooks:
        - tag.before_create: Can modify/validate data or block
        - tag.after_create: Notification of successful creation

        Args:
            request: Tag creation request
            user_id: The current user's ID
            is_admin: Whether the caller is an administrator (MODEL tags are
                global and may only be authored by admins)

        Raises:
            ValueError: If invalid type or creation blocked

        Returns:
            The created tag
        """
        # Validate type
        if request.type not in [TagType.MODEL, *self._USER_SCOPED_TYPES]:
            raise ValueError("Invalid tag type. Must be MODEL, GENERATION or UPLOAD")

        # MODEL tags are global (shared by every user); only an admin may author them.
        if request.type == TagType.MODEL and not is_admin:
            raise ValueError("Tag not found or access denied")

        effective_user_id = self._get_user_id_for_type(request.type, user_id)

        # Execute before_create hook
        hook_data, blocked = execute_hook(self.plugins,
            TAG_HOOKS.before_create,
            {
                "name": request.name,
                "type": request.type.value,
                "user_id": effective_user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Tag creation blocked")
            logger.warning(f"Tag creation blocked by plugin: {reason}")
            raise ValueError(reason)

        # Allow hooks to modify name
        name = hook_data.get("name", request.name)

        # Create the tag
        tag = self.repository.create_tag(
            name=name,
            type=request.type.value,
            user_id=effective_user_id
        )

        if not tag:
            raise ValueError("Failed to create tag")

        # Execute after_create hook
        execute_hook(self.plugins,
            TAG_HOOKS.after_create,
            {
                "tag_id": tag.id,
                "name": tag.name,
                "type": tag.type,
                "user_id": tag.user_id
            }
        )

        logger.info(f"Tag created: {tag.name} (id: {tag.id}, type: {tag.type})")
        return tag

    # ========== Update Operations ==========

    def update_tag(self, tag_id: str, request: UpdateTagRequest, user_id: str, is_admin: bool = False) -> Tag:
        """
        Update a tag's name.

        Executes hooks:
        - tag.before_update: Can modify/validate data or block
        - tag.after_update: Notification of successful update

        Args:
            tag_id: The tag ID to update
            request: Tag update request
            user_id: The current user's ID
            is_admin: Whether the caller is an administrator (MODEL tags are
                global and may only be edited by admins)

        Raises:
            ValueError: If tag not found, access denied, or update blocked

        Returns:
            The updated tag
        """
        # Get existing tag and verify access
        existing = self.repository.get_tag_by_id(tag_id)
        if not existing:
            raise ValueError("Tag not found or access denied")

        # For user-owned tag types, verify ownership
        if existing.type in self._USER_SCOPED_TYPES and existing.user_id != user_id:
            raise ValueError("Tag not found or access denied")

        # MODEL tags are global; only an admin may edit them.
        if existing.type == TagType.MODEL and not is_admin:
            raise ValueError("Tag not found or access denied")

        # Execute before_update hook
        hook_data, blocked = execute_hook(self.plugins,
            TAG_HOOKS.before_update,
            {
                "tag_id": tag_id,
                "old_name": existing.name,
                "new_name": request.name,
                "type": existing.type,
                "user_id": existing.user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Tag update blocked")
            logger.warning(f"Tag update blocked by plugin: {reason}")
            raise ValueError(reason)

        # Allow hooks to modify name
        name = hook_data.get("new_name", request.name)

        # Perform update
        success = self.repository.update_tag(tag_id, name)
        if not success:
            raise ValueError("Failed to update tag")

        # Fetch updated tag
        updated_tag = self.repository.get_tag_by_id(tag_id)
        if not updated_tag:
            raise ValueError("Failed to retrieve updated tag")

        # Execute after_update hook
        execute_hook(self.plugins,
            TAG_HOOKS.after_update,
            {
                "tag_id": tag_id,
                "name": updated_tag.name,
                "type": updated_tag.type,
                "user_id": updated_tag.user_id
            }
        )

        logger.info(f"Tag updated: {updated_tag.name} (id: {tag_id})")
        return updated_tag

    # ========== Delete Operations ==========

    def delete_tag(self, tag_id: str, user_id: str, is_admin: bool = False) -> bool:
        """
        Delete a tag.

        Executes hooks:
        - tag.before_delete: Can block deletion
        - tag.after_delete: Notification of successful deletion

        Args:
            tag_id: The tag ID to delete
            user_id: The current user's ID
            is_admin: Whether the caller is an administrator (MODEL tags are
                global and may only be deleted by admins)

        Raises:
            ValueError: If tag not found, access denied, or deletion blocked

        Returns:
            True if deleted successfully
        """
        # Get existing tag and verify access
        existing = self.repository.get_tag_by_id(tag_id)
        if not existing:
            raise ValueError("Tag not found or access denied")

        # For user-owned tag types, verify ownership
        if existing.type in self._USER_SCOPED_TYPES and existing.user_id != user_id:
            raise ValueError("Tag not found or access denied")

        # MODEL tags are global; only an admin may delete them.
        if existing.type == TagType.MODEL and not is_admin:
            raise ValueError("Tag not found or access denied")

        # Refuse to delete a tag an admin has wired into a preset's stored
        # configuration (e.g. `model_tags`) - no force flag, unset it from the
        # preset's configuration first. See docs/presets.md "Configuration (admin-set)".
        used_by_refs = self.preset_repository.find_presets_referencing_tag(tag_id)
        if used_by_refs:
            used_by = []
            for ref in used_by_refs:
                preset_template = self.file_preset_repository.find_preset_by_id(ref["preset_id"])
                used_by.append({
                    "preset_id": ref["preset_id"],
                    "preset_name": preset_template.name if preset_template else ref["preset_id"],
                    "key": ref["key"],
                })
            raise TagInUseByPresetError(tag_id, used_by)

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            TAG_HOOKS.before_delete,
            {
                "tag_id": tag_id,
                "name": existing.name,
                "type": existing.type,
                "user_id": existing.user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Tag deletion blocked")
            logger.warning(f"Tag deletion blocked by plugin: {reason}")
            raise ValueError(reason)

        # Perform deletion
        success = self.repository.delete_tag(tag_id)
        if not success:
            raise ValueError("Failed to delete tag")

        # Execute after_delete hook
        execute_hook(self.plugins,
            TAG_HOOKS.after_delete,
            {
                "tag_id": tag_id,
                "name": existing.name,
                "type": existing.type,
                "user_id": existing.user_id
            }
        )

        logger.info(f"Tag deleted: {existing.name} (id: {tag_id})")
        return True
