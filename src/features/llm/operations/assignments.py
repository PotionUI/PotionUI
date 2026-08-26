"""
Assign/unassign an LLM configuration to/from a user.

Module-level functions, collaborators as explicit leading args - no class
holds them together. Assignment reads (list a user's configs, list a
config's users, the assignment-count summary) are pure repository reads and
stay in the controller - see `LLMController`.
"""
import logging

from src.features.llm.exceptions import (
    ConfigurationNotFoundException,
    AssignmentNotFoundException,
    AssignmentFailedException,
)
from src.features.llm.repository import LLMRepository

logger = logging.getLogger(__name__)


def assign_llm_to_user(repo: LLMRepository, user_id: str, llm_config_id: str) -> dict:
    """
    Assign an LLM configuration to a user.

    Raises:
        ConfigurationNotFoundException: If LLM config not found
        AssignmentFailedException: If assignment fails
    """
    config = repo.get_configuration(llm_config_id)
    if not config:
        raise ConfigurationNotFoundException(
            f"LLM configuration '{llm_config_id}' not found"
        )

    success = repo.assign_llm_to_user(user_id, llm_config_id)

    if not success:
        raise AssignmentFailedException("Failed to assign LLM to user")

    logger.info(f"LLM '{config.name}' assigned to user {user_id}")
    return {"user_id": user_id, "llm_config_id": llm_config_id}


def unassign_llm_from_user(repo: LLMRepository, user_id: str, llm_config_id: str) -> dict:
    """
    Remove LLM configuration assignment from a user.

    Raises:
        AssignmentNotFoundException: If assignment not found
        AssignmentFailedException: If unassignment fails
    """
    is_assigned = repo.is_llm_assigned_to_user(user_id, llm_config_id)
    if not is_assigned:
        raise AssignmentNotFoundException("LLM assignment not found for user")

    success = repo.unassign_llm_from_user(user_id, llm_config_id)

    if not success:
        raise AssignmentFailedException("Failed to remove LLM assignment")

    logger.info(f"LLM '{llm_config_id}' unassigned from user {user_id}")
    return {"user_id": user_id, "llm_config_id": llm_config_id}
