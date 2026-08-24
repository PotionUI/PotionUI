"""Assigning and unassigning models to users."""

import logging
from typing import Any, Dict

from src.features.models.access_policy import ModelAccessPolicy
from src.features.models.exceptions import ModelAssignmentException
from src.platform.plugins.hooks import execute_hook
from src.features.models.hooks import MODEL_INDEX_HOOKS
from src.features.models.repository import ModelRepository
from src.platform.plugins import PluginRegistry

logger = logging.getLogger(__name__)


class ModelAssignmentService:
    """Manages the user<->model assignment table, gated by plugin hooks.

    Defers to ModelAccessPolicy to turn an opaque insert failure into a precise
    reason (already assigned, unknown model, or unknown user).
    """

    def __init__(
        self,
        model_repository: ModelRepository,
        plugin_registry: PluginRegistry,
        access_policy: ModelAccessPolicy,
    ):
        self.model_repo = model_repository
        self.plugins = plugin_registry
        self.access_policy = access_policy

    def get_user_model_assignments(self, user_id: str) -> Dict[str, Any]:
        """List the models assigned to a user."""
        assignments = self.model_repo.get_user_models(user_id)
        return {
            "user_id": user_id,
            "assignments": [a.to_dict() for a in assignments]
        }

    def get_model_assignments(self, model_id: str) -> Dict[str, Any]:
        """List the users directly assigned to a model."""
        assignments = self.model_repo.get_model_users(model_id)
        return {
            "model_id": model_id,
            "assignments": [a.to_dict() for a in assignments]
        }

    def get_assignment_summary(self) -> Dict[str, Dict[str, int]]:
        """Direct-user and group assignment counts, keyed by model_id."""
        return self.model_repo.get_model_assignment_summary()

    def assign_model_to_user(self, model_id: str, user_id: str) -> Dict[str, Any]:
        """Assign a model to a user.

        Fires model_index.before_assign (can block) and after_assign. Raises
        ModelAssignmentException (or the ModelAlreadyAssignedException subtype) on failure.
        """
        hook_data, blocked = execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.before_assign,
            {"model_id": model_id, "user_id": user_id}
        )

        if blocked:
            reason = hook_data.get("block_reason", "Assignment blocked by plugin")
            raise ModelAssignmentException(reason)

        if not model_id or not user_id:
            raise ModelAssignmentException(
                f"Assignment needs both a model_id and a user_id "
                f"(got model_id={model_id!r}, user_id={user_id!r})"
            )

        assignment = self.model_repo.assign_model_to_user(model_id, user_id)
        if not assignment:
            raise self.access_policy.explain_assignment_failure(model_id, user_id)

        execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.after_assign,
            {"model_id": model_id, "user_id": user_id, "assignment_id": assignment.id}
        )

        return {
            "assignment": assignment.to_dict(),
            "message": "Model assigned to user successfully"
        }

    def unassign_model_from_user(self, model_id: str, user_id: str) -> Dict[str, Any]:
        """Unassign a model from a user.

        Fires model_index.before_unassign (can block) and after_unassign. Raises
        ModelAssignmentException if the assignment does not exist or is vetoed.
        """
        hook_data, blocked = execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.before_unassign,
            {"model_id": model_id, "user_id": user_id}
        )

        if blocked:
            reason = hook_data.get("block_reason", "Unassignment blocked by plugin")
            raise ModelAssignmentException(reason)

        removed = self.model_repo.unassign_model_from_user(model_id, user_id)
        if not removed:
            raise ModelAssignmentException("Model assignment not found")

        execute_hook(
            self.plugins,
            MODEL_INDEX_HOOKS.after_unassign,
            {"model_id": model_id, "user_id": user_id}
        )

        return {"message": "Model unassigned from user successfully"}
