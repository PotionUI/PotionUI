import re
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "***"

_SENSITIVE_NAME = re.compile(r"authorization|api[-_]?key|token|secret|password|cookie|signature|credential|(^|[-_])key($|[-_])", re.IGNORECASE)
_BEARER = re.compile(r"\b(Bearer|Basic|Key|Token)\s+[A-Za-z0-9._~+/=\-]{6,}", re.IGNORECASE)


def is_sensitive_name(name: str) -> bool:
    return bool(_SENSITIVE_NAME.search(name))


def redact_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    return {name: REDACTED if is_sensitive_name(name) else str(value) for name, value in headers.items()}


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.netloc.rsplit("@", 1)[-1]
    query = urlencode([(name, REDACTED) for name, _ in parse_qsl(parts.query, keep_blank_values=True)], safe="*")
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def secret_fragments(values: Iterable[str]) -> tuple[str, ...]:
    fragments: set[str] = set()
    for value in values:
        value = str(value).strip()
        if not value:
            continue
        fragments.add(value)
        tail = value.split()[-1]
        if len(tail) >= 6:
            fragments.add(tail)
    return tuple(sorted(fragments, key=len, reverse=True))


def scrub_text(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return _BEARER.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
