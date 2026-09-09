"""First-run setup: atomic instance claiming, registration policy, status, and
the readiness aggregate.

Executing a recipe is not setup's job - the wizard drives the recipes feature
(`src.features.recipes`). What stays here is what only the first run needs:
the instance claim, the public status the login screen routes on, per-user
onboarding state, and the `workspace.activate` step kind that closes
onboarding out.
"""

from src.features.setup.onboarding_repository import OnboardingRepository
from src.features.setup.repository import InstanceClaimRepository
from src.features.setup.workspace_activate import WorkspaceActivateExecutor

__all__ = [
    "InstanceClaimRepository",
    "OnboardingRepository",
    "WorkspaceActivateExecutor",
]
