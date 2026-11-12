from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from src.features.sessions.request import (
    LAYOUT_KEYS,
    OVERRIDE_KEYS,
    SessionModeMissing,
    apply_overrides,
    mode_payload,
)

__all__ = ["LAYOUT_KEYS", "OVERRIDE_KEYS", "SessionModeMissing", "apply_overrides", "mode_payload", "session_copy"]


def session_copy(snapshot: Mapping[str, Any], mode: str) -> Dict[str, Any]:
    payload = mode_payload(snapshot.get("data") or {}, mode)
    source = {
        "id": snapshot.get("session_id"),
        "name": snapshot.get("name"),
        "version": snapshot.get("version"),
        "unversioned": snapshot.get("version") is None,
        "preset_id": snapshot.get("preset_id"),
        "mode": mode,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"session_source": source, "session_snapshot": payload}
