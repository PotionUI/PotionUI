import asyncio
import ipaddress
import os
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp

from src.features.automation.http_request import BlockedTargetError, PublicOnlyResolver
from src.platform.http.targets import is_public_address
from src.platform.http.tls import certificate_failure_message, client_ssl_context, is_certificate_error

ALLOW_LOCAL_SOURCES_ENV = "POTIONUI_PLUGIN_CATALOG_ALLOW_LOCAL"
MAX_REDIRECTS = 5

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHUNK = 65536

Lookup = Callable[..., Awaitable[Any]]


class FetchError(Exception):
    pass


def local_sources_allowed() -> bool:
    return os.environ.get(ALLOW_LOCAL_SOURCES_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _blocked(host: str) -> FetchError:
    return FetchError(
        f"{host} is a local or private address; set {ALLOW_LOCAL_SOURCES_ENV}=1 to install from such a source."
    )


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
    if allow_local:
        return
    name = host.rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        raise _blocked(host)
    try:
        ipaddress.ip_address(name.split("%", 1)[0])
    except ValueError:
        return
    if not is_public_address(name):
        raise _blocked(host)


async def fetch_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout_s: float,
    allow_local: bool,
    lookup: Optional[Lookup] = None,
) -> bytes:
    connector = aiohttp.TCPConnector(
        ssl=client_ssl_context(),
        resolver=PublicOnlyResolver(allow_local, lookup),
        use_dns_cache=False,
    )
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        for _ in range(MAX_REDIRECTS + 1):
            check_source_url(url, allow_local)
            host = urlsplit(url).hostname or url
            try:
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
                raise FetchError(f"{host} did not answer within {timeout_s:g}s.") from error
            except aiohttp.ClientConnectorError as error:
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
