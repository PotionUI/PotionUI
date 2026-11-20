from typing import Any, Callable, Dict, List, Optional, Set

from src.platform.http.errors import DomainError

KEPT_MODE_KEYS = ("selectedVariant", "presetVersion")

OwnedFields = Callable[[str, str], Set[str]]
ModeCheck = Callable[[str, str], str]

REFUSED_MODE_CODES = frozenset({"session_mode_required", "session_mode_unknown", "session_mode_not_offered"})

def incoming_modes(data: Any, mode: Optional[str]) -> Optional[List[str]]:
    if mode:
        return [mode]
    if not isinstance(data, dict) or not data:
        return None
    if all(isinstance(value, dict) for value in data.values()):
        return list(data)
    selected = data.get("selectedMode")
    if isinstance(selected, str) and selected:
        return [selected]
    return None


def check_editor_modes(data: Any, mode: Optional[str], preset_id: str, mode_check: Optional[ModeCheck]) -> None:
    modes = incoming_modes(data, mode)
    if modes is None:
        raise DomainError(
            "session_mode_required", "An editor session must name the mode it belongs to", 422
        )
    for name in modes:
        status = mode_check(preset_id, name) if mode_check else "ok"
        if status == "unknown":
            raise DomainError(
                "session_mode_unknown", f"'{name}' is not an editor mode of this preset", 422, mode=name
            )
        if status == "not_offered":
            raise DomainError(
                "session_mode_not_offered", f"The editor mode '{name}' offers no editor task", 422, mode=name
            )


def strip_editor_data(data: Any, preset_id: str, owned_fields: Optional[OwnedFields]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    stripped: Dict[str, Any] = {}
    for stored_mode, payload in data.items():
        if not isinstance(payload, dict):
            continue
        owned = owned_fields(preset_id, stored_mode) if owned_fields else set()
        form = payload.get("formData")
        entry: Dict[str, Any] = {
            "formData": {key: value for key, value in form.items() if key not in owned} if isinstance(form, dict) else {}
        }
        for key in KEPT_MODE_KEYS:
            if key in payload:
                entry[key] = payload[key]
        stripped[stored_mode] = entry
    return stripped
