"""
Preset/LLM/model assignment to a group.

Module-level functions, collaborators as explicit leading args - no class
holds them together. The three resource kinds share one hook pair
(``before/after_assign_resource``, ``before/after_unassign_resource``) keyed
by a ``resource_type`` string (see ``USER_GROUP_HOOKS``'s docstring) and
identical list/assign/unassign shape, so each kind is a thin wrapper over a
shared private implementation rather than three near-duplicate copies.
"""
import logging
from typing import Callable, List

from src.features.user_groups.dto import UserGroupLLMDTO, UserGroupModelDTO, UserGroupPresetDTO
from src.features.user_groups.hooks import USER_GROUP_HOOKS
from src.features.user_groups.mappers import llm_to_dto, model_to_dto, preset_to_dto
from src.features.user_groups.operations.guards import require_admin, require_group_exists
from src.features.user_groups.repository import UserGroupRepository
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.platform.security.user import User

logger = logging.getLogger(__name__)


def _get_group_resources(repository: UserGroupRepository, group_id: str, user: User, list_fn: Callable, to_dto: Callable) -> List:
    require_admin(user)
    require_group_exists(repository, group_id)

    return [to_dto(item) for item in list_fn(group_id)]


def _assign_resources(
    repository: UserGroupRepository,
    plugins: PluginRegistry,
    group_id: str,
    resource_ids: List[str],
    user: User,
    resource_type: str,
    assign_fn: Callable,
    to_dto: Callable,
) -> List:
    """Assign resources of one kind to a group (duplicates are skipped)."""
    require_admin(user)
    require_group_exists(repository, group_id)

    assigned = []
    for resource_id in resource_ids:
        # Execute before_assign_resource hook
        hook_data, blocked = execute_hook(
            plugins,
            USER_GROUP_HOOKS.before_assign_resource,
            {
                "group_id": group_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "admin_id": user.id,
            },
        )

        if blocked:
            reason = hook_data.get("block_reason", f"{resource_type.capitalize()} assignment blocked")
            logger.warning(f"{resource_type.capitalize()} assignment blocked by plugin: {reason}")
            continue

        assignment = assign_fn(group_id, resource_id)
        if assignment:
            assigned.append(to_dto(assignment))

            # Execute after_assign_resource hook
            execute_hook(
                plugins,
                USER_GROUP_HOOKS.after_assign_resource,
                {
                    "group_id": group_id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "assignment_id": assignment.id,
                },
            )

    logger.info(f"Assigned {len(assigned)} {resource_type}s to group {group_id}")
    return assigned


def _unassign_resource(
    repository: UserGroupRepository,
    plugins: PluginRegistry,
    group_id: str,
    resource_id: str,
    user: User,
    resource_type: str,
    unassign_fn: Callable,
    not_assigned_message: str,
) -> bool:
    """Unassign a resource of one kind from a group."""
    require_admin(user)
    require_group_exists(repository, group_id)

    # Execute before_unassign_resource hook
    hook_data, blocked = execute_hook(
        plugins,
        USER_GROUP_HOOKS.before_unassign_resource,
        {"group_id": group_id, "resource_type": resource_type, "resource_id": resource_id, "admin_id": user.id},
    )

    if blocked:
        reason = hook_data.get("block_reason", f"{resource_type.capitalize()} unassignment blocked")
        logger.warning(f"{resource_type.capitalize()} unassignment blocked by plugin: {reason}")
        raise ValueError(reason)

    removed = unassign_fn(group_id, resource_id)
    if not removed:
        raise ValueError(not_assigned_message)

    # Execute after_unassign_resource hook
    execute_hook(
        plugins,
        USER_GROUP_HOOKS.after_unassign_resource,
        {"group_id": group_id, "resource_type": resource_type, "resource_id": resource_id},
    )

    logger.info(f"Unassigned {resource_type} {resource_id} from group {group_id}")
    return True


# ========== Presets ==========

def get_group_presets(repository: UserGroupRepository, group_id: str, user: User) -> List[UserGroupPresetDTO]:
    """Get all presets assigned to a group.

    Raises:
        ValueError: If user is not admin or group not found
    """
    return _get_group_resources(repository, group_id, user, repository.get_group_presets, preset_to_dto)


def assign_presets(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, preset_ids: List[str], user: User
) -> List[UserGroupPresetDTO]:
    """Assign presets to a group.

    Raises:
        ValueError: If user is not admin or group not found

    Returns:
        List of assigned presets (duplicates are skipped)
    """
    return _assign_resources(
        repository, plugins, group_id, preset_ids, user, "preset", repository.assign_preset_to_group, preset_to_dto
    )


def unassign_preset(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, preset_id: str, user: User
) -> bool:
    """Unassign a preset from a group.

    Raises:
        ValueError: If user is not admin, group not found, or preset not assigned
    """
    return _unassign_resource(
        repository, plugins, group_id, preset_id, user, "preset",
        repository.unassign_preset_from_group, "Preset is not assigned to this group",
    )


# ========== LLMs ==========

def get_group_llms(repository: UserGroupRepository, group_id: str, user: User) -> List[UserGroupLLMDTO]:
    """Get all LLM configurations assigned to a group.

    Raises:
        ValueError: If user is not admin or group not found
    """
    return _get_group_resources(repository, group_id, user, repository.get_group_llms, llm_to_dto)


def assign_llms(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, llm_config_ids: List[str], user: User
) -> List[UserGroupLLMDTO]:
    """Assign LLM configurations to a group.

    Raises:
        ValueError: If user is not admin or group not found

    Returns:
        List of assigned LLMs (duplicates are skipped)
    """
    return _assign_resources(
        repository, plugins, group_id, llm_config_ids, user, "llm", repository.assign_llm_to_group, llm_to_dto
    )


def unassign_llm(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, llm_config_id: str, user: User
) -> bool:
    """Unassign an LLM configuration from a group.

    Raises:
        ValueError: If user is not admin, group not found, or LLM not assigned
    """
    return _unassign_resource(
        repository, plugins, group_id, llm_config_id, user, "llm",
        repository.unassign_llm_from_group, "LLM configuration is not assigned to this group",
    )


# ========== Models ==========

def get_group_models(repository: UserGroupRepository, group_id: str, user: User) -> List[UserGroupModelDTO]:
    """Get all models assigned to a group.

    Raises:
        ValueError: If user is not admin or group not found
    """
    return _get_group_resources(repository, group_id, user, repository.get_group_models, model_to_dto)


def assign_models(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, model_ids: List[str], user: User
) -> List[UserGroupModelDTO]:
    """Assign models to a group.

    Raises:
        ValueError: If user is not admin or group not found

    Returns:
        List of assigned models (duplicates are skipped)
    """
    return _assign_resources(
        repository, plugins, group_id, model_ids, user, "model", repository.assign_model_to_group, model_to_dto
    )


def unassign_model(
    repository: UserGroupRepository, plugins: PluginRegistry, group_id: str, model_id: str, user: User
) -> bool:
    """Unassign a model from a group.

    Raises:
        ValueError: If user is not admin, group not found, or model not assigned
    """
    return _unassign_resource(
        repository, plugins, group_id, model_id, user, "model",
        repository.unassign_model_from_group, "Model is not assigned to this group",
    )
