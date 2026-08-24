"""Request-origin helpers shared by the auth and setup features.

Deciding whether a request came from the local machine is a cross-feature
concern (registration gating needs it, the setup-status endpoint reports it),
so it lives on the platform HTTP layer rather than inside one feature.
"""

from __future__ import annotations

import ipaddress
from typing import Optional

# Hostnames that resolve to the loopback interface but are not IP literals.
_LOOPBACK_HOSTNAMES = {"localhost"}


def is_loopback_host(host: Optional[str]) -> bool:
    """Return True when `host` (a request client host) is the local machine.

    Accepts the ``localhost`` hostname and any loopback IP literal, including
    IPv4-mapped IPv6 forms such as ``::ffff:127.0.0.1``. Anything that is not a
    parseable loopback address - including a proxy's forwarded address - is
    treated as remote, which is the safe default for the token gate that calls
    this: an unknown origin is required to present the setup token.
    """
    if not host:
        return False

    candidate = host.strip()
    if not candidate:
        return False
    if candidate.casefold() in _LOOPBACK_HOSTNAMES:
        return True

    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False

    if address.is_loopback:
        return True

    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped is not None and mapped.is_loopback)
