"""
Response mappers for the user_groups feature.

Plain functions that turn UserGroup/UserGroupMember/UserGroupPreset/
UserGroupLLM/UserGroupModel records into their API response DTOs. No class,
no state - `group_to_counts_dto` needs the repository to fetch the four
per-resource counts, so it takes it as an explicit argument.
"""
from typing import Optional

from src.features.user_groups.dto import (
    UserGroupDTO,
    UserGroupMemberDTO,
    UserGroupPresetDTO,
    UserGroupLLMDTO,
    UserGroupModelDTO,
    GroupWithCountsDTO,
)
from src.features.user_groups.repository import UserGroupRepository


def group_to_dto(obj) -> Optional[UserGroupDTO]:
    """Convert a UserGroup dataclass model to UserGroupDTO."""
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


def group_to_counts_dto(obj, repository: UserGroupRepository) -> Optional[GroupWithCountsDTO]:
    """Convert a UserGroup dataclass model to GroupWithCountsDTO, enriched
    with the group's member/preset/LLM/model counts."""
    if obj is None:
        return None
    return GroupWithCountsDTO(
        id=obj.id,
        name=obj.name,
        description=obj.description,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
        member_count=repository.get_group_member_count(obj.id),
        preset_count=repository.get_group_preset_count(obj.id),
        llm_count=repository.get_group_llm_count(obj.id),
        model_count=repository.get_group_model_count(obj.id),
        is_system=obj.is_system,
    )


def member_to_dto(obj) -> Optional[UserGroupMemberDTO]:
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


def preset_to_dto(obj) -> Optional[UserGroupPresetDTO]:
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


def llm_to_dto(obj) -> Optional[UserGroupLLMDTO]:
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


def model_to_dto(obj) -> Optional[UserGroupModelDTO]:
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
