import asyncio
import ipaddress
import os
import re
import socket
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp

from src.platform.http.targets import BlockedTargetError, PublicOnlyResolver, is_public_address
from src.platform.http.tls import certificate_failure_message, client_ssl_context, is_certificate_error

ALLOW_LOCAL_SOURCES_ENV = "POTIONUI_PLUGIN_CATALOG_ALLOW_LOCAL"
MAX_REDIRECTS = 5
READ_TIMEOUT_S = 30.0

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_NUMERIC_HOST = re.compile(r"(0x[0-9a-f]*|[0-9]+)([.](0x[0-9a-f]*|[0-9]*))*", re.IGNORECASE)
_CHUNK = 65536

Lookup = Callable[..., Awaitable[Any]]


class FetchError(Exception):
    pass


class _PublicOverPlainHttp(BlockedTargetError):
    pass


class _SourceResolver(PublicOnlyResolver):
    def __init__(self, allow_local: bool, local_only: bool, lookup: Optional[Lookup] = None):
        super().__init__(allow_local, lookup)
        self._local_only = local_only

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> List[Dict[str, Any]]:
        records = await super().resolve(host, port, family)
        if self._local_only and any(is_public_address(record["host"]) for record in records):
            raise _PublicOverPlainHttp(f"{host} resolves to a public address")
        return records


def local_sources_allowed() -> bool:
    return os.environ.get(ALLOW_LOCAL_SOURCES_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _blocked(host: str) -> FetchError:
    return FetchError(
        f"{host} is a local or private address; set {ALLOW_LOCAL_SOURCES_ENV}=1 to install from such a source."
    )


def _plain_http_to_public(host: str) -> FetchError:
    return FetchError(
        f"Plain http is only allowed to a local or private address, and {host} is public; use https."
    )


def _literal_address(host: str) -> Optional[str]:
    name = host.rstrip(".").split("%", 1)[0]
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return None
    return name


def check_source_url(url: str, allow_local: bool) -> None:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme != "https" and not (allow_local and scheme == "http"):
        raise FetchError(f"Only https URLs are allowed, got {url!r}.")
    host = parts.hostname
    if not host:
        raise FetchError(f"The URL {url!r} has no host.")
    if parts.username or parts.password:
        raise FetchError("Credentials inside the URL are not allowed.")
    try:
        parts.port
    except ValueError as error:
        raise FetchError(f"The URL {url!r} has an invalid port.") from error
    address = _literal_address(host)
    if address is None and _NUMERIC_HOST.fullmatch(host.rstrip(".")):
        raise FetchError(f"{host} is a number but not an IP address written in full; write the address as such.")
    if allow_local:
        if scheme == "http" and address is not None and is_public_address(address):
            raise _plain_http_to_public(host)
        return
    name = host.rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        raise _blocked(host)
    if address is not None and not is_public_address(address):
        raise _blocked(host)


async def fetch_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout_s: float,
    allow_local: bool,
    lookup: Optional[Lookup] = None,
    read_timeout_s: float = READ_TIMEOUT_S,
) -> bytes:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    for _ in range(MAX_REDIRECTS + 1):
        check_source_url(url, allow_local)
        host = urlsplit(url).hostname or url
        local_only = urlsplit(url).scheme.lower() == "http"
        connector = aiohttp.TCPConnector(
            ssl=client_ssl_context(),
            resolver=_SourceResolver(allow_local, local_only, lookup),
            use_dns_cache=False,
        )
        remaining = max(deadline - loop.time(), 0.001)
        timeout = aiohttp.ClientTimeout(
            total=remaining, sock_connect=min(read_timeout_s, remaining), sock_read=min(read_timeout_s, remaining)
        )
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(url, allow_redirects=False) as response:
                    if response.status in _REDIRECT_STATUSES:
                        location = response.headers.get("Location")
                        if not location:
                            raise FetchError(f"{host} sent a redirect without a location.")
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise FetchError(f"{host} answered HTTP {response.status} for {url}.")
                    if response.content_length is not None and response.content_length > max_bytes:
                        raise FetchError(f"{url} is larger than {max_bytes} bytes.")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(_CHUNK):
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise FetchError(f"{url} is larger than {max_bytes} bytes.")
                    return bytes(body)
        except asyncio.TimeoutError as error:
            raise FetchError(
                f"{host} did not answer within {timeout_s:g}s, or sent nothing for {read_timeout_s:g}s."
            ) from error
        except aiohttp.ClientConnectorError as error:
            if isinstance(error.os_error, _PublicOverPlainHttp):
                raise _plain_http_to_public(host) from error
            if isinstance(error.os_error, BlockedTargetError):
                raise _blocked(host) from error
            if is_certificate_error(error):
                raise FetchError(certificate_failure_message(host)) from error
            raise FetchError(f"Could not reach {host}.") from error
        except aiohttp.ClientError as error:
            if is_certificate_error(error):
                raise FetchError(certificate_failure_message(host)) from error
            raise FetchError(f"{type(error).__name__} while fetching {url}.") from error
    raise FetchError(f"More than {MAX_REDIRECTS} redirects while fetching the source.")
