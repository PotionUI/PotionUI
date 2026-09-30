import asyncio
import ipaddress
import json as json_module
import logging
import math
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp

from src.features.cloud.clock import Clock, MonotonicClock
from src.features.cloud.contracts import CloudBackendConfig, CloudError, CloudProvider
from src.features.cloud.rate_limit import TokenBucket
from src.features.cloud.redaction import is_sensitive_name, redact_headers, redact_url, scrub_text, secret_fragments
from src.platform.database.rows import now_utc
from src.platform.http.tls import aiohttp_connector, certificate_failure_message, is_certificate_error

logger = logging.getLogger(__name__)

ErrorMapper = Callable[[int, Mapping[str, str], Any], Optional[CloudError]]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ERROR_BODY_LIMIT = 65536
_DETAIL_LIMIT = 500
_CHUNK = 65536
RETRY_AFTER_CAP_S = 300.0
_ABSOLUTE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_DEFAULT_PORTS = {"http": 80, "https": 443}

_INVALID_REQUEST = frozenset({400, 404, 405, 409, 413, 415, 422})

_USER_MESSAGES = {
    "auth": "The provider rejected the API key. Check it in Administration, Backends.",
    "credits": "The provider account is out of credits.",
    "refused": "The provider refused this request.",
    "timeout": "The provider took too long to answer.",
    "rate_limited": "The provider is rate limiting requests. Try again shortly.",
    "unavailable": "The provider is temporarily unavailable.",
    "invalid_request": "The provider rejected the request parameters.",
    "failed": "The provider could not complete the request.",
}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json_module.loads(self.body)
        except ValueError as error:
            raise CloudError(
                "failed",
                "The provider sent an answer that could not be read.",
                detail=f"invalid JSON (HTTP {self.status}): {error}",
            ) from error


def parse_retry_after(value: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        seconds = (moment - (now if now is not None else now_utc())).total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, RETRY_AFTER_CAP_S)


def _body_message(body: Any) -> str:
    if isinstance(body, Mapping):
        error = body.get("error", body)
        if isinstance(error, Mapping):
            message = error.get("message") or error.get("detail")
            if message:
                return str(message)
        elif isinstance(error, str):
            return error
        message = body.get("message") or body.get("detail")
        if message:
            return str(message)
        return json_module.dumps(body, default=str)
    return str(body)


def default_error_mapper(status: int, headers: Mapping[str, str], body: Any) -> Optional[CloudError]:
    if status < 400:
        return None
    retry_after = parse_retry_after(headers.get("retry-after"))
    if status == 401:
        kind = "auth"
    elif status == 402:
        kind = "credits"
    elif status == 403:
        kind = "refused"
    elif status in (408, 504):
        kind = "timeout"
    elif status == 429:
        kind = "rate_limited"
    elif status >= 500:
        kind = "unavailable"
    elif status in _INVALID_REQUEST:
        kind = "invalid_request"
    else:
        kind = "failed"
    detail = f"HTTP {status}: {_body_message(body)}"[:_DETAIL_LIMIT]
    return CloudError(kind, _USER_MESSAGES[kind], detail=detail, retry_after_s=retry_after)


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme, 0)


class CloudHttp:
    def __init__(
        self,
        base_url: str,
        *,
        auth_headers: Optional[Mapping[str, str]] = None,
        timeout_s: float = 60.0,
        max_parallel: int = 4,
        requests_per_second: Optional[float] = None,
        burst: int = 1,
        clock: Optional[Clock] = None,
        error_mapper: Optional[ErrorMapper] = None,
        max_redirects: int = 5,
        allow_private_targets: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._origin = _origin(self._base_url)
        self._auth_headers = dict(auth_headers or {})
        self._secrets = secret_fragments(self._auth_headers.values())
        self._timeout_s = timeout_s
        self._max_redirects = max_redirects
        self._allow_private_targets = allow_private_targets
        self._clock = clock or MonotonicClock()
        self._bucket = TokenBucket(requests_per_second, max(1, burst), self._clock)
        self._slots = asyncio.Semaphore(max(1, max_parallel))
        self._error_mapper = error_mapper
        self._session: Optional[aiohttp.ClientSession] = None

    @classmethod
    def for_provider(
        cls,
        provider_class: type[CloudProvider],
        config: CloudBackendConfig,
        **overrides: Any,
    ) -> "CloudHttp":
        options: dict[str, Any] = {
            "base_url": provider_class.api_base_url(config),
            "auth_headers": provider_class.auth_headers(config),
            "max_parallel": config.max_parallel,
        }
        options.update(overrides)
        return cls(options.pop("base_url"), **options)

    @property
    def base_url(self) -> str:
        return self._base_url

    def set_error_mapper(self, mapper: Optional[ErrorMapper]) -> None:
        self._error_mapper = mapper

    def owns_url(self, url: str) -> bool:
        return _origin(url) == self._origin

    def pause(self, seconds: float) -> None:
        self._bucket.pause(seconds)

    def resolve(self, target: str) -> str:
        if _ABSOLUTE.match(target):
            return target
        return f"{self._base_url}/{target.lstrip('/')}"

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is not None and not session.closed:
            await session.close()

    async def __aenter__(self) -> "CloudHttp":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def request(
        self,
        method: str,
        target: str,
        *,
        json: Any = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout_s: Optional[float] = None,
    ) -> HttpResponse:
        async def read(response: aiohttp.ClientResponse) -> HttpResponse:
            return HttpResponse(response.status, _lower(response.headers), await response.read())

        return await self._exchange(
            method.upper(),
            self.resolve(target),
            json=json,
            params=params,
            headers=headers,
            extra_auth=None,
            timeout_s=timeout_s,
            follow=method.upper() in ("GET", "HEAD"),
            streaming=False,
            guard_targets=False,
            sink=read,
        )

    async def request_json(
        self,
        method: str,
        target: str,
        *,
        json: Any = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout_s: Optional[float] = None,
    ) -> Any:
        response = await self.request(method, target, json=json, params=params, headers=headers, timeout_s=timeout_s)
        return response.json()

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        auth_headers: Optional[Mapping[str, str]] = None,
        max_bytes: Optional[int] = None,
        timeout_s: Optional[float] = None,
    ) -> Path:
        dest = Path(dest)
        partial = dest.with_name(dest.name + ".part")
        dest.parent.mkdir(parents=True, exist_ok=True)

        async def stream(response: aiohttp.ClientResponse) -> int:
            declared = response.content_length
            if max_bytes is not None and declared is not None and declared > max_bytes:
                raise _too_large(max_bytes)
            written = 0
            with partial.open("wb") as handle:
                async for chunk in response.content.iter_chunked(_CHUNK):
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise _too_large(max_bytes)
                    handle.write(chunk)
            return written

        try:
            await self._exchange(
                "GET",
                self.resolve(url),
                json=None,
                params=None,
                headers=None,
                extra_auth=auth_headers,
                timeout_s=timeout_s,
                follow=True,
                streaming=True,
                guard_targets=not self._allow_private_targets,
                sink=stream,
            )
            os.replace(partial, dest)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return dest

    def _headers_for(
        self,
        url: str,
        headers: Optional[Mapping[str, str]],
        extra_auth: Optional[Mapping[str, str]],
    ) -> dict[str, str]:
        merged = dict(headers or {})
        if self.owns_url(url):
            merged.update(self._auth_headers)
            merged.update(extra_auth or {})
        return merged

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(connector=aiohttp_connector())
        return self._session

    def _timeout(self, timeout_s: Optional[float], streaming: bool) -> aiohttp.ClientTimeout:
        seconds = self._timeout_s if timeout_s is None else timeout_s
        if streaming:
            return aiohttp.ClientTimeout(total=None, sock_connect=seconds, sock_read=seconds)
        return aiohttp.ClientTimeout(total=seconds)

    async def _exchange(
        self,
        method: str,
        url: str,
        *,
        json: Any,
        params: Optional[Mapping[str, Any]],
        headers: Optional[Mapping[str, str]],
        extra_auth: Optional[Mapping[str, str]],
        timeout_s: Optional[float],
        follow: bool,
        streaming: bool,
        guard_targets: bool,
        sink: Callable[[aiohttp.ClientResponse], Awaitable[Any]],
    ) -> Any:
        session = await self._get_session()
        timeout = self._timeout(timeout_s, streaming)
        hops = 0
        secrets = self._secrets + secret_fragments(
            [*(extra_auth or {}).values(), *(v for n, v in (headers or {}).items() if is_sensitive_name(n))]
        )
        while True:
            if guard_targets and not self.owns_url(url):
                await _ensure_public_target(url)
            sent = self._headers_for(url, headers, extra_auth)
            logger.debug("cloud request %s %s headers=%s", method, redact_url(url), redact_headers(sent))
            await self._bucket.acquire()
            try:
                async with self._slots:
                    async with session.request(
                        method,
                        url,
                        json=json if hops == 0 else None,
                        params=params if hops == 0 else None,
                        headers=sent,
                        timeout=timeout,
                        allow_redirects=False,
                    ) as response:
                        status = response.status
                        if status in _REDIRECT_STATUSES and follow:
                            location = response.headers.get("Location")
                            hops += 1
                            if not location or hops > self._max_redirects:
                                raise CloudError("failed", _USER_MESSAGES["failed"], detail=f"bad redirect from {redact_url(url)}")
                            url = urljoin(url, location)
                            continue
                        logger.debug("cloud response %s %s -> %s", method, redact_url(url), status)
                        if status >= 300:
                            raise await self._error_for(response, url, secrets)
                        return await sink(response)
            except CloudError:
                raise
            except asyncio.TimeoutError as error:
                raise CloudError(
                    "timeout", _USER_MESSAGES["timeout"], detail=scrub_text(f"timed out: {method} {redact_url(url)}", secrets)
                ) from error
            except aiohttp.ClientError as error:
                host = urlsplit(url).hostname or url
                never_sent = isinstance(error, aiohttp.ClientConnectorError)
                if is_certificate_error(error):
                    raise CloudError(
                        "unavailable",
                        certificate_failure_message(host),
                        detail=scrub_text(str(error), secrets),
                        request_sent=not never_sent,
                    ) from error
                raise CloudError(
                    "unavailable",
                    f"Could not reach {host}.",
                    detail=scrub_text(f"{type(error).__name__}: {error}", secrets),
                    request_sent=not never_sent,
                ) from error

    async def _error_for(self, response: aiohttp.ClientResponse, url: str, secrets: tuple[str, ...]) -> CloudError:
        raw = await _read_limited(response, _ERROR_BODY_LIMIT)
        text = raw.decode("utf-8", errors="replace")
        try:
            body: Any = json_module.loads(text)
        except ValueError:
            body = text
        headers = _lower(response.headers)
        error = None
        if self._error_mapper is not None:
            error = self._error_mapper(response.status, headers, body)
        if error is None:
            error = default_error_mapper(response.status, headers, body)
        if error is None:
            error = CloudError("failed", _USER_MESSAGES["failed"], detail=f"HTTP {response.status}")
        error.detail = scrub_text(error.detail, secrets)[:_DETAIL_LIMIT]
        error.user_message = scrub_text(error.user_message, secrets)
        error.args = (error.user_message,)
        if error.retry_after_s is not None and not math.isfinite(error.retry_after_s):
            error.retry_after_s = None
        if error.retry_after_s is not None:
            error.retry_after_s = min(max(error.retry_after_s, 0.0), RETRY_AFTER_CAP_S)
        if error.kind == "rate_limited" and error.retry_after_s:
            self._bucket.pause(error.retry_after_s)
        logger.warning("cloud error %s %s -> %s (%s)", response.method, redact_url(url), response.status, error.kind)
        return error

def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


def _too_large(max_bytes: int) -> CloudError:
    return CloudError(
        "failed",
        "The result is larger than the allowed size.",
        detail=f"download exceeded {max_bytes} bytes",
    )


async def _read_limited(response: aiohttp.ClientResponse, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = await response.content.read(limit - total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved)


def _refused_target(host: str) -> CloudError:
    return CloudError(
        "failed",
        "The provider pointed to a download address that is not allowed.",
        detail=f"refused non-public target {host}",
    )


async def _ensure_public_target(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise CloudError(
            "failed",
            "The provider pointed to a download address that is not allowed.",
            detail=f"refused scheme {parts.scheme!r}",
        )
    host = parts.hostname
    if not host:
        raise _refused_target(url)
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        pass
    else:
        if not _is_public(host):
            raise _refused_target(host)
        return
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme.lower(), 0)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return
    for info in infos:
        if not _is_public(info[4][0]):
            raise _refused_target(host)
