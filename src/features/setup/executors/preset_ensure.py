"""`preset.ensure` - install the recipe's preset (if needed) and assign it to
the owner running setup, so it's ready to use the moment onboarding finishes.

`operations.install_preset`/`assign_preset_to_users` both require an admin
`User`; the owner is exactly that (the instance's claimed owner is always an
admin), resolved from `run.created_by` - the account that started this setup
run.
"""

from __future__ import annotations

from src.features.presets import operations
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.exceptions import (
    InvalidUsersException,
    PermissionDeniedException,
    PresetAlreadyInstalledException,
    PresetNotFoundException,
    PresetNotInstalledException,
)
from src.features.setup.executors.base import StepContext, StepResult
from src.features.users.repository import UserRepository


class PresetEnsureExecutor:
    def __init__(self, preset_manager: PresetCollaborators, user_repository: UserRepository):
        self.preset_manager = preset_manager
        self.user_repository = user_repository

    def execute(self, context: StepContext) -> StepResult:
        preset_id = context.step.params.get("preset_id")
        if not preset_id:
            return StepResult.fail(
                "PRESET_ENSURE_MISCONFIGURED",
                "This step doesn't say which preset to install.",
            )

        owner_id = context.owner_user_id
        owner = self.user_repository.get_by_id(owner_id) if owner_id else None
        if owner is None:
            return StepResult.fail(
                "OWNER_NOT_FOUND",
                "We couldn't find the account that started setup, so the preset can't be assigned automatically.",
                suggested_repair="Open Administration -> Presets and assign it to your account manually.",
            )

        if self.preset_manager.file_repo.find_preset_by_id(preset_id) is None:
            return StepResult.fail(
                "PRESET_MISSING_ON_DISK",
                f"The preset this setup needs ('{preset_id}') isn't available on this installation.",
                suggested_repair="Reinstall or update PotionUI so its bundled presets are present, then retry.",
            )

        try:
            operations.install_preset(self.preset_manager, preset_id, owner)
        except PresetAlreadyInstalledException:
            pass  # already installed - nothing to do, not an error
        except (PresetNotFoundException, PermissionDeniedException) as exc:
            return StepResult.fail("PRESET_INSTALL_FAILED", f"Installing the preset failed: {exc}")

        try:
            operations.assign_preset_to_users(self.preset_manager, preset_id, [owner.id], owner)
        except (PresetNotInstalledException, InvalidUsersException, PermissionDeniedException) as exc:
            return StepResult.fail("PRESET_ASSIGN_FAILED", f"Assigning the preset to your account failed: {exc}")

        return StepResult.ok({"preset_id": preset_id, "assigned_to": owner.id})
