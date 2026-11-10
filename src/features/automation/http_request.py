import asyncio
import ipaddress
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp

from src.features.cloud.redaction import is_sensitive_name, redact_headers, scrub_text, secret_fragments
from src.platform.http.targets import DEFAULT_PORTS, BlockedTargetError, PublicOnlyResolver, is_public_address, origin_of
from src.platform.http.tls import certificate_failure_message, client_ssl_context, is_certificate_error

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
BODY_MODES = ("none", "json", "form", "text")
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 120.0
MAX_RETRIES = 5
MAX_REDIRECTS = 5
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0
ERROR_BODY_LIMIT = 500

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRY_METHODS = frozenset({"GET", "PUT", "DELETE"})
_CHUNK = 65536


class HttpRequestError(Exception):
    pass


class TransportError(Exception):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class RequestSpec:
    method: str
    url: str
    query: Tuple[Tuple[str, str], ...] = ()
    headers: Tuple[Tuple[str, str], ...] = ()
    body_mode: str = "none"
    body: Any = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    expected_status: str = "2xx"
    retries: int = 0
    follow_redirects: bool = False
    allow_private_targets: bool = False
    secret_values: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedRequest:
    method: str
    url: str
    headers: Dict[str, str]
    body: Optional[bytes]
    timeout_s: float
    allow_private_targets: bool


@dataclass
class RawResponse:
    status: int
    headers: Dict[str, str]
    body: bytes
    truncated: bool = False


@dataclass
class HttpResult:
    status: int
    ok: bool
    headers: Dict[str, str]
    body: Any
    truncated: bool
    url: str
    attempts: int


Transport = Callable[[PreparedRequest], Awaitable[RawResponse]]


def validate_url(url: str, allow_private: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme.lower() not in DEFAULT_PORTS:
        raise HttpRequestError(f"Only http and https URLs are allowed, got '{parts.scheme or url}'.")
    host = parts.hostname
    if not host:
        raise HttpRequestError("The URL has no host.")
    if parts.username or parts.password:
        raise HttpRequestError("Credentials inside the URL are not allowed; use a header instead.")
    try:
        parts.port
    except ValueError as error:
        raise HttpRequestError("The URL has an invalid port.") from error
    if allow_private:
        return
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        raise HttpRequestError(f"Requests to {host} are blocked; enable private targets on the node to allow them.")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return
    if not is_public_address(host):
        raise HttpRequestError(f"Requests to {host} are blocked; enable private targets on the node to allow them.")


async def aiohttp_transport(request: PreparedRequest) -> RawResponse:
    connector = aiohttp.TCPConnector(
        ssl=client_ssl_context(),
        resolver=PublicOnlyResolver(request.allow_private_targets),
        use_dns_cache=False,
    )
    host = urlsplit(request.url).hostname or request.url
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=request.timeout_s)) as session:
            async with session.request(
                request.method,
                request.url,
                headers=request.headers,
                data=request.body,
                allow_redirects=False,
                auto_decompress=True,
            ) as response:
                chunks: List[bytes] = []
                size = 0
                truncated = False
                async for chunk in response.content.iter_chunked(_CHUNK):
                    room = MAX_RESPONSE_BYTES - size
                    if len(chunk) > room:
                        chunks.append(chunk[:room])
                        truncated = True
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                return RawResponse(response.status, dict(response.headers), b"".join(chunks), truncated)
    except asyncio.TimeoutError as error:
        raise TransportError(f"Timed out after {request.timeout_s:g}s.") from error
    except aiohttp.ClientConnectorError as error:
        if isinstance(error.os_error, BlockedTargetError):
            raise HttpRequestError(f"{error.os_error}; enable private targets on the node to allow it.") from error
        if is_certificate_error(error):
            raise TransportError(certificate_failure_message(host), retryable=False) from error
        raise TransportError(f"Could not reach {host}.") from error
    except aiohttp.ClientError as error:
        if is_certificate_error(error):
            raise TransportError(certificate_failure_message(host), retryable=False) from error
        raise TransportError(f"{type(error).__name__} while calling {host}.") from error


def status_matches(status: int, expected: str) -> bool:
    tokens = [t.strip().lower() for t in str(expected or "2xx").split(",") if t.strip()] or ["2xx"]
    for token in tokens:
        if len(token) == 3 and token.endswith("xx") and token[0].isdigit():
            if status // 100 == int(token[0]):
                return True
        elif "-" in token:
            low, _, high = token.partition("-")
            if low.strip().isdigit() and high.strip().isdigit() and int(low) <= status <= int(high):
                return True
        elif token.isdigit() and int(token) == status:
            return True
    return False


def _merge_query(url: str, query: Tuple[Tuple[str, str], ...]) -> str:
    if not query:
        return url
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True) + [(n, v) for n, v in query if n]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def _encode_body(spec: RequestSpec) -> Tuple[Optional[bytes], Optional[str]]:
    if spec.body_mode == "none":
        return None, None
    if spec.body_mode == "json":
        text = str(spec.body or "")
        try:
            json.loads(text)
        except ValueError as error:
            raise HttpRequestError(f"The request body is not valid JSON: {error}") from error
        payload, content_type = text.encode("utf-8"), "application/json"
    elif spec.body_mode == "form":
        rows = [(n, v) for n, v in (spec.body or ()) if n]
        payload, content_type = urlencode(rows).encode("utf-8"), "application/x-www-form-urlencoded"
    elif spec.body_mode == "text":
        payload, content_type = str(spec.body or "").encode("utf-8"), "text/plain; charset=utf-8"
    else:
        raise HttpRequestError(f"Unknown body type '{spec.body_mode}'.")
    if len(payload) > MAX_REQUEST_BYTES:
        raise HttpRequestError(f"The request body is larger than {MAX_REQUEST_BYTES // 1024} KB.")
    return payload, content_type


def _lower(headers: Mapping[str, str]) -> Dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _decode(raw: RawResponse) -> Any:
    headers = _lower(raw.headers)
    content_type = headers.get("content-type", "")
    charset = "utf-8"
    for piece in content_type.split(";")[1:]:
        name, _, value = piece.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip('"')
    try:
        text = raw.body.decode(charset, errors="replace")
    except LookupError:
        text = raw.body.decode("utf-8", errors="replace")
    if "json" in content_type.lower() and not raw.truncated:
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def _backoff(attempt: int) -> float:
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** attempt))


async def execute(
    spec: RequestSpec,
    transport: Transport = aiohttp_transport,
    *,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> HttpResult:
    method = spec.method.upper()
    if method not in METHODS:
        raise HttpRequestError(f"Unsupported method '{spec.method}'.")
    timeout_s = max(1.0, min(float(spec.timeout_s or DEFAULT_TIMEOUT_S), MAX_TIMEOUT_S))
    retries = max(0, min(int(spec.retries or 0), MAX_RETRIES))
    payload, content_type = _encode_body(spec)
    headers = {str(n): str(v) for n, v in spec.headers if n}
    if content_type and not any(n.lower() == "content-type" for n in headers):
        headers["Content-Type"] = content_type
    secrets = secret_fragments([*(v for n, v in headers.items() if is_sensitive_name(n)), *spec.secret_values])
    url = _merge_query(spec.url.strip(), spec.query)
    deadline = clock() + timeout_s
    attempt = 0
    hops = 0
    while True:
        validate_url(url, spec.allow_private_targets)
        remaining = min(timeout_s, deadline - clock())
        if remaining <= 0:
            raise HttpRequestError(f"Timed out after {timeout_s:g}s.")
        prepared = PreparedRequest(method, url, headers, payload, remaining, spec.allow_private_targets)
        can_retry = attempt < retries and method in _RETRY_METHODS
        try:
            raw = await transport(prepared)
        except TransportError as error:
            if can_retry and error.retryable:
                await sleep(min(_backoff(attempt), max(0.0, deadline - clock())))
                attempt += 1
                continue
            raise HttpRequestError(scrub_text(str(error), secrets)) from error
        if raw.status in _REDIRECT_STATUSES and spec.follow_redirects:
            location = {k.lower(): v for k, v in raw.headers.items()}.get("location")
            hops += 1
            if not location or hops > MAX_REDIRECTS:
                raise HttpRequestError("The server sent a redirect that cannot be followed.")
            target = urljoin(url, location)
            if origin_of(target) != origin_of(url):
                raise HttpRequestError("The server redirected to a different origin; redirects stay on the same origin.")
            if raw.status == 303 or raw.status in (301, 302) and method == "POST":
                method, payload = "GET", None
                headers = {n: v for n, v in headers.items() if n.lower() not in ("content-type", "content-length")}
            url = target
            continue
        if raw.status >= 500 and can_retry:
            await sleep(min(_backoff(attempt), max(0.0, deadline - clock())))
            attempt += 1
            continue
        break
    body = _decode(raw)
    ok = status_matches(raw.status, spec.expected_status)
    if not ok:
        detail = body if isinstance(body, str) else json.dumps(body)
        detail = scrub_text(detail, secrets)[:ERROR_BODY_LIMIT]
        raise HttpRequestError(f"HTTP {raw.status}: {detail}" if detail else f"HTTP {raw.status}")
    return HttpResult(
        status=raw.status,
        ok=ok,
        headers=redact_headers(_lower(raw.headers)),
        body=body,
        truncated=raw.truncated,
        url=url,
        attempts=attempt + 1,
    )
