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
    """Decode a TEXT column holding an ISO timestamp.

    Falsy -> None, an already-parsed `datetime` passes through, a malformed
    string yields None rather than raising.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def now_iso() -> str:
    """Current UTC time as the ISO string repositories stamp rows with."""
    return datetime.now(timezone.utc).isoformat()
