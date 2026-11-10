import asyncio
import ipaddress
import socket
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from aiohttp.abc import AbstractResolver

DEFAULT_PORTS = {"http": 80, "https": 443}

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in _SIX_TO_FOUR:
        return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    return None


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(ip)
        if embedded is not None:
            ip = embedded
    return ip.is_global and not ip.is_multicast


def origin_of(url: str) -> Tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or DEFAULT_PORTS.get(scheme, 0)


class BlockedTargetError(OSError):
    pass


class PublicOnlyResolver(AbstractResolver):
    def __init__(self, allow_private: bool, lookup: Optional[Callable[..., Awaitable[Any]]] = None):
        self._allow_private = allow_private
        self._lookup = lookup

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> List[Dict[str, Any]]:
        lookup = self._lookup or asyncio.get_running_loop().getaddrinfo
        infos = await lookup(host, port, type=socket.SOCK_STREAM)
        records: List[Dict[str, Any]] = []
        for info in infos:
            address = info[4][0]
            if not self._allow_private and not is_public_address(address):
                raise BlockedTargetError(f"{host} resolves to a non-public address")
            records.append({
                "hostname": host,
                "host": address,
                "port": port,
                "family": info[0],
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            })
        if not records:
            raise OSError(f"{host} did not resolve")
        return records

    async def close(self) -> None:
        return None
