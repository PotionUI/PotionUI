import copy
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

LAYOUT_KEYS = frozenset(
    {
        "workbenchMaxHeight",
        "leftPanelWidth",
        "leftPanelCollapsed",
        "workbenchCollapsed",
        "layoutMode",
        "promptPanelWidth",
        "positiveSegmentsCollapsed",
        "negativeSegmentsCollapsed",
        "sectionCollapsed",
    }
)

OVERRIDE_KEYS = frozenset(
    {
        "formData",
        "prompt",
        "negativePrompt",
        "promptSegments",
        "negativePromptSegments",
        "promptTabs",
        "activePromptTab",
        "promptRelay",
        "variables",
        "seed",
        "videoDirector",
        "musicDirector",
    }
)


class SessionModeMissing(LookupError):
    pass


def mode_payload(session_data: Mapping[str, Any], mode: str) -> Dict[str, Any]:
    payload = (session_data or {}).get(mode)
    if not isinstance(payload, Mapping):
        raise SessionModeMissing(mode)
    return {key: copy.deepcopy(value) for key, value in payload.items() if key not in LAYOUT_KEYS}


def apply_overrides(snapshot: Optional[Mapping[str, Any]], overrides: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    effective = copy.deepcopy(dict(snapshot or {}))
    for key, value in (overrides or {}).items():
        if key == "formData":
            merged = dict(effective.get("formData") or {})
            merged.update(copy.deepcopy(value or {}))
            effective["formData"] = merged
        else:
            effective[key] = copy.deepcopy(value)
    return effective


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
