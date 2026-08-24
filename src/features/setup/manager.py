"""First-run setup status.

Answers the single public question a fresh client asks before it can route:
does this instance still need an owner, is registration open, and (for a remote
client) is a setup token required to claim it? All heavier gating - actually
enforcing the policy and the token - lives in `AuthManager.register`; this
manager only reports state.
"""

from typing import TYPE_CHECKING

from src.features.setup.dto import SetupStatus
from src.features.setup.repository import InstanceClaimRepository

if TYPE_CHECKING:
    from src.platform.security.claim_token import ClaimTokenManager
    from src.platform.settings.settings import SettingsManager


class SetupManager:
    """Computes the public setup status from claim state, policy, and token."""

    def __init__(
        self,
        instance_claim_repository: InstanceClaimRepository,
        claim_token_manager: "ClaimTokenManager",
        settings_manager: "SettingsManager",
    ):
        self.instance_claim = instance_claim_repository
        self.claim_tokens = claim_token_manager
        self.settings = settings_manager

    def registration_open(self) -> bool:
        """Whether register() will currently accept a new account.

        Always open while unclaimed (someone must become the owner); once
        claimed, governed by the ``registration_policy`` setting (default
        ``closed``).
        """
        if not self.instance_claim.is_claimed():
            return True
        policy = (self.settings.get_setting("registration_policy", "closed") or "closed")
        return policy.strip().lower() == "open"

    def status(self, is_loopback: bool) -> SetupStatus:
        """Public status for a request from `is_loopback` origin.

        `claim_requires_token` is True only for a remote client that still needs
        to claim an unclaimed instance and for which a token exists to present;
        a loopback operator is trusted and never needs one.
        """
        needs_owner = not self.instance_claim.is_claimed()
        claim_requires_token = (
            needs_owner and not is_loopback and self.claim_tokens.exists()
        )
        return SetupStatus(
            needs_owner=needs_owner,
            registration_open=self.registration_open(),
            claim_requires_token=claim_requires_token,
        )
