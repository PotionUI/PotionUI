"""The instance-claim store as authentication sees it.

`AuthManager` must know whether this instance already has an owner before it
decides how to treat a registration, but the concrete persistence is a feature
(`src.features.setup.repository`) and platform code may not reach into a
feature. As with `UserStore`, the dependency is expressed structurally: anything
with these read methods can answer the question authentication asks.
"""

from __future__ import annotations

from typing import Optional, Protocol


class InstanceClaimStore(Protocol):
    """The slice of instance-claim persistence authentication depends on."""

    def is_claimed(self) -> bool:
        """True once the single-row claim sentinel exists (an owner was created)."""
        ...

    def owner_user_id(self) -> Optional[str]:
        """The id of the account that claimed the instance, or None if unclaimed."""
        ...
