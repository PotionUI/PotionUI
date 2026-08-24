"""Setup-status DTO.

Deliberately minimal: the setup-status endpoint is public and unauthenticated,
so it exposes only what a first-run client needs to route on and nothing about
the host (no hardware, paths, versions, or user details).
"""

from pydantic import BaseModel


class SetupStatus(BaseModel):
    """Public first-run status. These three booleans are the entire contract."""

    needs_owner: bool
    registration_open: bool
    claim_requires_token: bool
