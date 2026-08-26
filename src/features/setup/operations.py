"""First-run setup status.

Answers the single public question a fresh client asks before it can route:
does this instance still need an owner, is registration open, and (for a remote
client) is a setup token required to claim it? All heavier gating - actually
enforcing the policy and the token - lives in `AuthManager.register`; this
module only reports state.
"""

from typing import TYPE_CHECKING

from src.features.setup.dto import SetupStatus
from src.features.setup.repository import InstanceClaimRepository

if TYPE_CHECKING:
    from src.platform.security.claim_token import ClaimTokenManager
    from src.platform.settings.settings import SettingsManager


def registration_open(
    instance_claim: InstanceClaimRepository,
    settings: "SettingsManager",
) -> bool:
    """Whether register() will currently accept a new account.

    Always open while unclaimed (someone must become the owner); once
    claimed, governed by the ``registration_policy`` setting (default
    ``closed``).
    """
    if not instance_claim.is_claimed():
        return True
    policy = (settings.get_setting("registration_policy", "closed") or "closed")
    return policy.strip().lower() == "open"


def status(
    instance_claim: InstanceClaimRepository,
    claim_tokens: "ClaimTokenManager",
    settings: "SettingsManager",
    is_loopback: bool,
) -> SetupStatus:
    """Public status for a request from `is_loopback` origin.

    `claim_requires_token` is True only for a remote client that still needs
    to claim an unclaimed instance and for which a token exists to present;
    a loopback operator is trusted and never needs one.
    """
    needs_owner = not instance_claim.is_claimed()
    claim_requires_token = (
        needs_owner and not is_loopback and claim_tokens.exists()
    )
    return SetupStatus(
        needs_owner=needs_owner,
        registration_open=registration_open(instance_claim, settings),
        claim_requires_token=claim_requires_token,
    )
