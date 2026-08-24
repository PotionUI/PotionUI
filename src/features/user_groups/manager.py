"""
User Group domain manager.

Handles all business logic for user groups. Framework-agnostic - uses ValueError
for errors (controller converts to HTTP responses).
"""
import logging
from typing import List, Optional


from src.features.user_groups.dto import (
    UserGroupDTO,
    UserGroupMemberDTO,
    UserGroupPresetDTO,
    UserGroupLLMDTO,
    UserGroupModelDTO,
    GroupWithCountsDTO,
    GroupCreate,
    GroupUpdate,
)
from src.features.user_groups.repository import UserGroupRepository
from src.platform.security.user import User, AccountType
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.user_groups.hooks import USER_GROUP_HOOKS

logger = logging.getLogger(__name__)


class SystemGroupProtectedError(ValueError):
    """Raised when deletion of a built-in (`is_system=True`) group is attempted.

    A `ValueError` subclass so anything only expecting the base type still
    behaves reasonably; the controller catches this specific type first to
    surface HTTP 409 with a plain message instead of the generic 400 the
    routes give other `ValueError`s (see `UserGroupController.delete_group`).
    """

    def __init__(self, group_name: str):
        self.group_name = group_name
        super().__init__(f"'{group_name}' is a built-in group and can't be deleted.")


class UserGroupManager:
    """
    Coordinates user group operations.

    Handles CRUD for groups, member management, and resource assignments.
    All operations require admin permissions.
    """

    def __init__(
        self,
        user_group_repository: UserGroupRepository,
        plugin_registry: PluginRegistry
    ):
        self.repository = user_group_repository
        self.plugins = plugin_registry

    def _require_admin(self, user: User) -> None:
        """
        Verify the user has admin permissions.

        Args:
            user: The user to check

        Raises:
            ValueError: If user is not an admin
        """
        if user.account_type != AccountType.ADMIN:
            raise ValueError("Admin access required")

    def _require_group_exists(self, group_id: str) -> UserGroupDTO:
        """
        Verify a group exists and return it.

        Args:
            group_id: The group ID to check

        Raises:
            ValueError: If group does not exist

        Returns:
            The group DTO
        """
        group = self.repository.get_group_by_id(group_id)
        if not group:
            raise ValueError("User group not found")
        return self._dataclass_to_dto(group)

    def _dataclass_to_dto(self, obj) -> UserGroupDTO:
        """Convert a dataclass model to a Pydantic DTO."""
        if obj is None:
            return None
        return UserGroupDTO(
            id=obj.id,
            name=obj.name,
            description=obj.description,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
            is_system=obj.is_system,
        )

    def _member_to_dto(self, obj) -> UserGroupMemberDTO:
        """Convert a member dataclass to DTO."""
        if obj is None:
            return None
        return UserGroupMemberDTO(
            id=obj.id,
            group_id=obj.group_id,
            user_id=obj.user_id,
            assigned_at=obj.assigned_at,
            updated_at=obj.updated_at
        )

    def _preset_to_dto(self, obj) -> UserGroupPresetDTO:
        """Convert a preset assignment dataclass to DTO."""
        if obj is None:
            return None
        return UserGroupPresetDTO(
            id=obj.id,
            group_id=obj.group_id,
            preset_id=obj.preset_id,
            assigned_at=obj.assigned_at,
            updated_at=obj.updated_at
        )

    def _llm_to_dto(self, obj) -> UserGroupLLMDTO:
        """Convert an LLM assignment dataclass to DTO."""
        if obj is None:
            return None
        return UserGroupLLMDTO(
            id=obj.id,
            group_id=obj.group_id,
            llm_config_id=obj.llm_config_id,
            assigned_at=obj.assigned_at,
            updated_at=obj.updated_at
        )

    def _model_to_dto(self, obj) -> UserGroupModelDTO:
        """Convert a model assignment dataclass to DTO."""
        if obj is None:
            return None
        return UserGroupModelDTO(
            id=obj.id,
            group_id=obj.group_id,
            model_id=obj.model_id,
            assigned_at=obj.assigned_at,
            updated_at=obj.updated_at
        )

    # ========== Group CRUD Operations ==========

    def get_all_groups(self, user: User) -> List[GroupWithCountsDTO]:
        """
        Get all user groups with resource counts.

        Args:
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not an admin

        Returns:
            List of groups with counts
        """
        self._require_admin(user)

        groups = self.repository.get_all_groups()
        result = []
        for group in groups:
            result.append(GroupWithCountsDTO(
                id=group.id,
                name=group.name,
                description=group.description,
                created_at=group.created_at,
                updated_at=group.updated_at,
                member_count=self.repository.get_group_member_count(group.id),
                preset_count=self.repository.get_group_preset_count(group.id),
                llm_count=self.repository.get_group_llm_count(group.id),
                model_count=self.repository.get_group_model_count(group.id),
                is_system=group.is_system,
            ))
        return result

    def create_group(self, request: GroupCreate, user: User) -> UserGroupDTO:
        """
        Create a new user group.

        Executes hooks:
        - user_group.before_create: Can modify/validate data or block
        - user_group.after_create: Notification of successful creation

        Args:
            request: Group creation request
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin, name exists, or creation blocked

        Returns:
            The created group
        """
        self._require_admin(user)

        # Check for duplicate name
        existing = self.repository.get_group_by_name(request.name)
        if existing:
            raise ValueError("A group with this name already exists")

        # Execute before_create hook
        hook_data, blocked = execute_hook(self.plugins,
            USER_GROUP_HOOKS.before_create,
            {
                "name": request.name,
                "description": request.description,
                "user_id": user.id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Group creation blocked")
            logger.warning(f"Group creation blocked by plugin: {reason}")
            raise ValueError(reason)

        # Allow hooks to modify data
        name = hook_data.get("name", request.name)
        description = hook_data.get("description", request.description)

        # Create the group
        group = self.repository.create_group(name=name, description=description)

        if not group:
            raise ValueError("Failed to create group")

        # Execute after_create hook
        execute_hook(self.plugins,
            USER_GROUP_HOOKS.after_create,
            {
                "group_id": group.id,
                "name": group.name,
                "description": group.description
            }
        )

        logger.info(f"Group created: {group.name} (id: {group.id})")
        return self._dataclass_to_dto(group)

    def get_group(self, group_id: str, user: User) -> GroupWithCountsDTO:
        """
        Get a specific group with resource counts.

        Args:
            group_id: The group ID
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin or group not found

        Returns:
            The group with counts
        """
        self._require_admin(user)
        group = self._require_group_exists(group_id)

        return GroupWithCountsDTO(
            id=group.id,
            name=group.name,
            description=group.description,
            created_at=group.created_at,
            updated_at=group.updated_at,
            member_count=self.repository.get_group_member_count(group_id),
            preset_count=self.repository.get_group_preset_count(group_id),
            llm_count=self.repository.get_group_llm_count(group_id),
            model_count=self.repository.get_group_model_count(group_id),
            is_system=group.is_system,
        )

    def update_group(self, group_id: str, request: GroupUpdate, user: User) -> UserGroupDTO:
        """
        Update a user group.

        Executes hooks:
        - user_group.before_update: Can modify/validate data or block
        - user_group.after_update: Notification of successful update

        Args:
            group_id: The group ID to update
            request: Group update request
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin, group not found, name exists, or update blocked

        Returns:
            The updated group
        """
        self._require_admin(user)
        existing_group = self._require_group_exists(group_id)

        # Check for duplicate name if name is being changed
        if request.name is not None:
            existing = self.repository.get_group_by_name(request.name)
            if existing and existing.id != group_id:
                raise ValueError("A group with this name already exists")

        # Execute before_update hook
        hook_data, blocked = execute_hook(self.plugins,
            USER_GROUP_HOOKS.before_update,
            {
                "group_id": group_id,
                "old_name": existing_group.name,
                "new_name": request.name,
                "old_description": existing_group.description,
                "new_description": request.description,
                "user_id": user.id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Group update blocked")
            logger.warning(f"Group update blocked by plugin: {reason}")
            raise ValueError(reason)

        # Allow hooks to modify data
        name = hook_data.get("new_name", request.name)
        description = hook_data.get("new_description", request.description)

        # Update the group
        updated = self.repository.update_group(group_id, name=name, description=description)

        if not updated:
            raise ValueError("Failed to update group")

        # Execute after_update hook
        execute_hook(self.plugins,
            USER_GROUP_HOOKS.after_update,
            {
                "group_id": group_id,
                "name": updated.name,
                "description": updated.description
            }
        )

        logger.info(f"Group updated: {updated.name} (id: {group_id})")
        return self._dataclass_to_dto(updated)

    def delete_group(self, group_id: str, user: User) -> str:
        """
        Delete a user group.

        Executes hooks:
        - user_group.before_delete: Can block deletion
        - user_group.after_delete: Notification of successful deletion

        Args:
            group_id: The group ID to delete
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin, group not found, or deletion blocked
            SystemGroupProtectedError: If the group is a built-in group (is_system)

        Returns:
            The name of the deleted group
        """
        self._require_admin(user)
        group = self._require_group_exists(group_id)

        if group.is_system:
            raise SystemGroupProtectedError(group.name)

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            USER_GROUP_HOOKS.before_delete,
            {
                "group_id": group_id,
                "name": group.name,
                "user_id": user.id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Group deletion blocked")
            logger.warning(f"Group deletion blocked by plugin: {reason}")
            raise ValueError(reason)

        # Delete the group
        success = self.repository.delete_group(group_id)
        if not success:
            raise ValueError("Failed to delete group")

        # Execute after_delete hook
        execute_hook(self.plugins,
            USER_GROUP_HOOKS.after_delete,
            {
                "group_id": group_id,
                "name": group.name
            }
        )

        logger.info(f"Group deleted: {group.name} (id: {group_id})")
        return group.name

    # ========== Member Operations ==========

    def get_group_members(self, group_id: str, user: User) -> List[UserGroupMemberDTO]:
        """
        Get all members of a group.

        Args:
            group_id: The group ID
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin or group not found

        Returns:
            List of group members
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        members = self.repository.get_group_members(group_id)
        return [self._member_to_dto(m) for m in members]

    def add_members(self, group_id: str, user_ids: List[str], user: User) -> List[UserGroupMemberDTO]:
        """
        Add users to a group.

        Executes hooks for each member:
        - user_group.before_add_member: Can block addition
        - user_group.after_add_member: Notification of successful addition

        Args:
            group_id: The group ID
            user_ids: List of user IDs to add
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin or group not found

        Returns:
            List of added members (duplicates are skipped)
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        added = []
        for user_id in user_ids:
            # Execute before_add_member hook
            hook_data, blocked = execute_hook(self.plugins,
                USER_GROUP_HOOKS.before_add_member,
                {
                    "group_id": group_id,
                    "user_id": user_id,
                    "admin_id": user.id
                }
            )

            if blocked:
                reason = hook_data.get("block_reason", "Member addition blocked")
                logger.warning(f"Member addition blocked by plugin: {reason}")
                continue

            member = self.repository.add_user_to_group(group_id, user_id)
            if member:
                added.append(self._member_to_dto(member))

                # Execute after_add_member hook
                execute_hook(self.plugins,
                    USER_GROUP_HOOKS.after_add_member,
                    {
                        "group_id": group_id,
                        "user_id": user_id,
                        "member_id": member.id
                    }
                )

        logger.info(f"Added {len(added)} members to group {group_id}")
        return added

    def remove_member(self, group_id: str, user_id: str, user: User) -> bool:
        """
        Remove a user from a group.

        Executes hooks:
        - user_group.before_remove_member: Can block removal
        - user_group.after_remove_member: Notification of successful removal

        Args:
            group_id: The group ID
            user_id: The user ID to remove
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin, group not found, or member not found

        Returns:
            True if removed
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        # Execute before_remove_member hook
        hook_data, blocked = execute_hook(self.plugins,
            USER_GROUP_HOOKS.before_remove_member,
            {
                "group_id": group_id,
                "user_id": user_id,
                "admin_id": user.id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Member removal blocked")
            logger.warning(f"Member removal blocked by plugin: {reason}")
            raise ValueError(reason)

        removed = self.repository.remove_user_from_group(group_id, user_id)
        if not removed:
            raise ValueError("User is not a member of this group")

        # Execute after_remove_member hook
        execute_hook(self.plugins,
            USER_GROUP_HOOKS.after_remove_member,
            {
                "group_id": group_id,
                "user_id": user_id
            }
        )

        logger.info(f"Removed user {user_id} from group {group_id}")
        return True

    def get_user_groups(self, user_id: str, user: User) -> List[UserGroupDTO]:
        """
        Get all groups a user belongs to.

        Args:
            user_id: The user ID to look up
            user: The requesting user (must be admin)

        Raises:
            ValueError: If requesting user is not admin

        Returns:
            List of groups the user belongs to
        """
        self._require_admin(user)

        groups = self.repository.get_user_groups(user_id)
        return [self._dataclass_to_dto(g) for g in groups]

    # ========== Preset Operations ==========

    def get_group_presets(self, group_id: str, user: User) -> List[UserGroupPresetDTO]:
        """
        Get all presets assigned to a group.

        Args:
            group_id: The group ID
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin or group not found

        Returns:
            List of preset assignments
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        presets = self.repository.get_group_presets(group_id)
        return [self._preset_to_dto(p) for p in presets]

    def assign_presets(self, group_id: str, preset_ids: List[str], user: User) -> List[UserGroupPresetDTO]:
        """
        Assign presets to a group.

        Executes hooks for each preset:
        - user_group.before_assign_resource: Can block assignment
        - user_group.after_assign_resource: Notification of successful assignment

        Args:
            group_id: The group ID
            preset_ids: List of preset IDs to assign
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin or group not found

        Returns:
            List of assigned presets (duplicates are skipped)
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        assigned = []
        for preset_id in preset_ids:
            # Execute before_assign_resource hook
            hook_data, blocked = execute_hook(self.plugins,
                USER_GROUP_HOOKS.before_assign_resource,
                {
                    "group_id": group_id,
                    "resource_type": "preset",
                    "resource_id": preset_id,
                    "admin_id": user.id
                }
            )

            if blocked:
                reason = hook_data.get("block_reason", "Preset assignment blocked")
                logger.warning(f"Preset assignment blocked by plugin: {reason}")
                continue

            assignment = self.repository.assign_preset_to_group(group_id, preset_id)
            if assignment:
                assigned.append(self._preset_to_dto(assignment))

                # Execute after_assign_resource hook
                execute_hook(self.plugins,
                    USER_GROUP_HOOKS.after_assign_resource,
                    {
                        "group_id": group_id,
                        "resource_type": "preset",
                        "resource_id": preset_id,
                        "assignment_id": assignment.id
                    }
                )

        logger.info(f"Assigned {len(assigned)} presets to group {group_id}")
        return assigned

    def unassign_preset(self, group_id: str, preset_id: str, user: User) -> bool:
        """
        Unassign a preset from a group.

        Executes hooks:
        - user_group.before_unassign_resource: Can block unassignment
        - user_group.after_unassign_resource: Notification of successful unassignment

        Args:
            group_id: The group ID
            preset_id: The preset ID to unassign
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin, group not found, or preset not assigned

        Returns:
            True if unassigned
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        # Execute before_unassign_resource hook
        hook_data, blocked = execute_hook(self.plugins,
            USER_GROUP_HOOKS.before_unassign_resource,
            {
                "group_id": group_id,
                "resource_type": "preset",
                "resource_id": preset_id,
                "admin_id": user.id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Preset unassignment blocked")
            logger.warning(f"Preset unassignment blocked by plugin: {reason}")
            raise ValueError(reason)

        removed = self.repository.unassign_preset_from_group(group_id, preset_id)
        if not removed:
            raise ValueError("Preset is not assigned to this group")

        # Execute after_unassign_resource hook
        execute_hook(self.plugins,
            USER_GROUP_HOOKS.after_unassign_resource,
            {
                "group_id": group_id,
                "resource_type": "preset",
                "resource_id": preset_id
            }
        )

        logger.info(f"Unassigned preset {preset_id} from group {group_id}")
        return True

    # ========== LLM Operations ==========

    def get_group_llms(self, group_id: str, user: User) -> List[UserGroupLLMDTO]:
        """
        Get all LLM configurations assigned to a group.

        Args:
            group_id: The group ID
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin or group not found

        Returns:
            List of LLM assignments
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        llms = self.repository.get_group_llms(group_id)
        return [self._llm_to_dto(l) for l in llms]

    def assign_llms(self, group_id: str, llm_config_ids: List[str], user: User) -> List[UserGroupLLMDTO]:
        """
        Assign LLM configurations to a group.

        Executes hooks for each LLM:
        - user_group.before_assign_resource: Can block assignment
        - user_group.after_assign_resource: Notification of successful assignment

        Args:
            group_id: The group ID
            llm_config_ids: List of LLM config IDs to assign
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin or group not found

        Returns:
            List of assigned LLMs (duplicates are skipped)
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        assigned = []
        for llm_config_id in llm_config_ids:
            # Execute before_assign_resource hook
            hook_data, blocked = execute_hook(self.plugins,
                USER_GROUP_HOOKS.before_assign_resource,
                {
                    "group_id": group_id,
                    "resource_type": "llm",
                    "resource_id": llm_config_id,
                    "admin_id": user.id
                }
            )

            if blocked:
                reason = hook_data.get("block_reason", "LLM assignment blocked")
                logger.warning(f"LLM assignment blocked by plugin: {reason}")
                continue

            assignment = self.repository.assign_llm_to_group(group_id, llm_config_id)
            if assignment:
                assigned.append(self._llm_to_dto(assignment))

                # Execute after_assign_resource hook
                execute_hook(self.plugins,
                    USER_GROUP_HOOKS.after_assign_resource,
                    {
                        "group_id": group_id,
                        "resource_type": "llm",
                        "resource_id": llm_config_id,
                        "assignment_id": assignment.id
                    }
                )

        logger.info(f"Assigned {len(assigned)} LLMs to group {group_id}")
        return assigned

    def unassign_llm(self, group_id: str, llm_config_id: str, user: User) -> bool:
        """
        Unassign an LLM configuration from a group.

        Executes hooks:
        - user_group.before_unassign_resource: Can block unassignment
        - user_group.after_unassign_resource: Notification of successful unassignment

        Args:
            group_id: The group ID
            llm_config_id: The LLM config ID to unassign
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin, group not found, or LLM not assigned

        Returns:
            True if unassigned
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        # Execute before_unassign_resource hook
        hook_data, blocked = execute_hook(self.plugins,
            USER_GROUP_HOOKS.before_unassign_resource,
            {
                "group_id": group_id,
                "resource_type": "llm",
                "resource_id": llm_config_id,
                "admin_id": user.id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "LLM unassignment blocked")
            logger.warning(f"LLM unassignment blocked by plugin: {reason}")
            raise ValueError(reason)

        removed = self.repository.unassign_llm_from_group(group_id, llm_config_id)
        if not removed:
            raise ValueError("LLM configuration is not assigned to this group")

        # Execute after_unassign_resource hook
        execute_hook(self.plugins,
            USER_GROUP_HOOKS.after_unassign_resource,
            {
                "group_id": group_id,
                "resource_type": "llm",
                "resource_id": llm_config_id
            }
        )

        logger.info(f"Unassigned LLM {llm_config_id} from group {group_id}")
        return True

    # ========== Model Operations ==========

    def get_group_models(self, group_id: str, user: User) -> List[UserGroupModelDTO]:
        """
        Get all models assigned to a group.

        Args:
            group_id: The group ID
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin or group not found

        Returns:
            List of model assignments
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        models = self.repository.get_group_models(group_id)
        return [self._model_to_dto(m) for m in models]

    def assign_models(self, group_id: str, model_ids: List[str], user: User) -> List[UserGroupModelDTO]:
        """
        Assign models to a group.

        Executes hooks for each model:
        - user_group.before_assign_resource: Can block assignment
        - user_group.after_assign_resource: Notification of successful assignment

        Args:
            group_id: The group ID
            model_ids: List of model IDs to assign
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin or group not found

        Returns:
            List of assigned models (duplicates are skipped)
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        assigned = []
        for model_id in model_ids:
            # Execute before_assign_resource hook
            hook_data, blocked = execute_hook(self.plugins,
                USER_GROUP_HOOKS.before_assign_resource,
                {
                    "group_id": group_id,
                    "resource_type": "model",
                    "resource_id": model_id,
                    "admin_id": user.id
                }
            )

            if blocked:
                reason = hook_data.get("block_reason", "Model assignment blocked")
                logger.warning(f"Model assignment blocked by plugin: {reason}")
                continue

            assignment = self.repository.assign_model_to_group(group_id, model_id)
            if assignment:
                assigned.append(self._model_to_dto(assignment))

                # Execute after_assign_resource hook
                execute_hook(self.plugins,
                    USER_GROUP_HOOKS.after_assign_resource,
                    {
                        "group_id": group_id,
                        "resource_type": "model",
                        "resource_id": model_id,
                        "assignment_id": assignment.id
                    }
                )

        logger.info(f"Assigned {len(assigned)} models to group {group_id}")
        return assigned

    def unassign_model(self, group_id: str, model_id: str, user: User) -> bool:
        """
        Unassign a model from a group.

        Executes hooks:
        - user_group.before_unassign_resource: Can block unassignment
        - user_group.after_unassign_resource: Notification of successful unassignment

        Args:
            group_id: The group ID
            model_id: The model ID to unassign
            user: The requesting user (must be admin)

        Raises:
            ValueError: If user is not admin, group not found, or model not assigned

        Returns:
            True if unassigned
        """
        self._require_admin(user)
        self._require_group_exists(group_id)

        # Execute before_unassign_resource hook
        hook_data, blocked = execute_hook(self.plugins,
            USER_GROUP_HOOKS.before_unassign_resource,
            {
                "group_id": group_id,
                "resource_type": "model",
                "resource_id": model_id,
                "admin_id": user.id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Model unassignment blocked")
            logger.warning(f"Model unassignment blocked by plugin: {reason}")
            raise ValueError(reason)

        removed = self.repository.unassign_model_from_group(group_id, model_id)
        if not removed:
            raise ValueError("Model is not assigned to this group")

        # Execute after_unassign_resource hook
        execute_hook(self.plugins,
            USER_GROUP_HOOKS.after_unassign_resource,
            {
                "group_id": group_id,
                "resource_type": "model",
                "resource_id": model_id
            }
        )

        logger.info(f"Unassigned model {model_id} from group {group_id}")
        return True
