"""Access control for models: who may see which model, and why an assignment failed."""

import logging
from typing import List, Optional

from src.features.models.exceptions import (
    ModelNotFoundException,
    ModelAccessDeniedException,
    ModelAssignmentException,
    ModelAlreadyAssignedException,
)
from src.features.models.records import Model
from src.features.models.repository import ModelRepository
from src.platform.security.user import User, AccountType

logger = logging.getLogger(__name__)


class ModelAccessPolicy:
    """Decides which models a user is allowed to reach, and explains assignment failures.

    The catalog and assignment role classes ask this policy the visibility and
    ownership questions so that permission logic lives in one place rather than
    being re-derived at each query.
    """

    def __init__(self, model_repository: ModelRepository):
        self.model_repo = model_repository

    def get_allowed_model_ids(self, user: User, all_models: bool = False) -> Optional[List[str]]:
        """The model IDs `user` may see, or None when every model is allowed.

        An admin asking for `all_models` is unrestricted (None); everyone else is
        scoped to the models assigned to them.
        """
        if all_models and user.account_type == AccountType.ADMIN:
            return None
        return self.model_repo.get_available_model_ids_for_user(user.id)

    def verify_model_access(self, model_id: str, user: User) -> Model:
        """Return the model if `user` may reach it, else raise.

        Raises ModelNotFoundException if the model does not exist, or
        ModelAccessDeniedException if it exists but is outside the user's scope.
        """
        model = self.model_repo.get_by_id(model_id, include_providers=False)
        if not model:
            raise ModelNotFoundException(f"Model '{model_id}' not found")

        if user.account_type != AccountType.ADMIN:
            allowed_ids = self.model_repo.get_available_model_ids_for_user(user.id)
            if model_id not in allowed_ids:
                raise ModelAccessDeniedException(f"Access denied to model '{model_id}'")

        return model

    def explain_assignment_failure(self, model_id: str, user_id: str) -> ModelAssignmentException:
        """Turn a failed insert into an exception that says what actually went wrong.

        `user_models` carries `UNIQUE(user_id, model_id)` and foreign keys onto
        both `users` and `models`. SQLite reports all of them as one opaque
        `IntegrityError`, so this re-queries to distinguish the specific cause
        (already assigned vs. invalid user/model id) instead of raising one
        generic message that covers all of them.
        """
        existing = self.model_repo.find_user_model_assignment(model_id, user_id)
        if existing:
            return ModelAlreadyAssignedException(
                f"Model '{model_id}' is already assigned to user '{user_id}'",
                assignment=existing,
            )

        if self.model_repo.get_by_id(model_id) is None:
            return ModelAssignmentException(f"No model with id '{model_id}'")

        # The model exists and the pair isn't taken, so the remaining foreign key
        # is the user's.
        return ModelAssignmentException(
            f"Could not assign model '{model_id}' to user '{user_id}' - no such user"
        )
