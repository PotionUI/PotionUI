"""Request-origin helpers shared by the auth and setup features.

Deciding whether a request came from the local machine is a cross-feature
concern (registration gating needs it, the setup-status endpoint reports it),
so it lives on the platform HTTP layer rather than inside one feature.
"""

from __future__ import annotations

import ipaddress
from typing import Iterable, List, Optional
from urllib.parse import urlsplit

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


def _header_values(headers, name: str) -> List[str]:
    getlist = getattr(headers, "getlist", None)
    if getlist is not None:
        return list(getlist(name))
    value = headers.get(name)
    return [] if value is None else [value]


def _address_of(value: str) -> str:
    candidate = value.strip().strip('"')
    if candidate.startswith("["):
        return candidate[1:].split("]", 1)[0]
    if candidate.count(":") == 1:
        return candidate.rsplit(":", 1)[0]
    return candidate


def _all_loopback(entries: Iterable[str]) -> bool:
    return all(is_loopback_host(_address_of(entry)) for entry in entries)


def _list_entries(headers, name: str) -> List[str]:
    return [part for value in _header_values(headers, name) for part in value.split(",")]


def _forwarded_entries(headers) -> List[str]:
    entries: List[str] = []
    for element in _list_entries(headers, "forwarded"):
        for pair in element.split(";"):
            key, _, value = pair.partition("=")
            if key.strip().casefold() == "for":
                entries.append(value)
    return entries


def is_local_request(request) -> bool:
    client = getattr(request, "client", None)
    if not client or not is_loopback_host(getattr(client, "host", None)):
        return False

    headers = request.headers
    for entries in (
        _list_entries(headers, "x-forwarded-for"),
        _list_entries(headers, "x-real-ip"),
        _forwarded_entries(headers),
        _list_entries(headers, "x-forwarded-host"),
    ):
        if not _all_loopback(entries):
            return False

    for origin in _header_values(headers, "origin"):
        try:
            hostname = urlsplit(origin.strip()).hostname
        except ValueError:
            return False
        if not is_loopback_host(hostname):
            return False
    return True
