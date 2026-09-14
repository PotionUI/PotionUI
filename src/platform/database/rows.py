"""Shared row-decode helpers for repositories reading raw sqlite rows.

Every repository stores JSON blobs and ISO timestamps as plain TEXT columns
and decodes them by hand on the way out; these three helpers are the byte-
equivalent decode logic that kept being copied into each repository module.
"""

import json
from datetime import datetime, timezone
from typing import Any, Optional


def json_column(value: Optional[str], default: Any = None) -> Any:
    """Decode a TEXT column holding JSON. Falsy or malformed values yield `default`."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def row_get(row: Any, column_name: str, default: Any = None) -> Any:
    """Read `row[column_name]`, coalescing a missing column or a stored NULL
    to `default` instead of raising `KeyError`/`IndexError`."""
    try:
        value = row[column_name]
        return value if value is not None else default
    except (KeyError, IndexError):
        return default


def dt_column(value: Any) -> Optional[datetime]:
    """Decode a TEXT column holding an ISO timestamp into an AWARE datetime.

    Stored timestamps are UTC by contract (sqlite's CURRENT_TIMESTAMP and
    `now_iso()` both are), so a string without an offset is read as UTC
    rather than as the server's local time. Falsy -> None, an already-parsed
    `datetime` passes through (made aware the same way), a malformed string
    yields None rather than raising.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    try:
        return as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def as_utc(value: datetime) -> datetime:
    """An aware UTC datetime: a naive input is taken to already be UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def dt_iso(value: Any) -> Optional[str]:
    """The ISO 8601 string a timestamp leaves the API as: always carries its
    UTC offset, so a browser in any timezone parses it as the instant it is.
    Accepts a datetime or a stored TEXT value; None/empty -> None."""
    parsed = dt_column(value)
    return parsed.isoformat() if parsed else None


def now_utc() -> datetime:
    """Current time as an aware UTC datetime, for code that computes before it stores."""
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """Current UTC time as the ISO string repositories stamp rows with."""
    return now_utc().isoformat()
