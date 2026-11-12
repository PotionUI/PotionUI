from typing import Any, Dict, List, Optional

from src.features.video_director.normalize import VideoDirectorValidationError

SIMPLE_MODES_KEY = "simple_modes"
DIRECTOR_MODE = "video"

_COMPOSITIONS = {
    "t2v": (False, False),
    "i2v": (True, False),
    "flf": (True, True),
}


def placeholder_name(field: str) -> str:
    return f"setup-check-{field}.png"


def simple_mode_spec(capabilities: Optional[Dict[str, Any]], mode: Optional[str]) -> Optional[Dict[str, Any]]:
    table = (capabilities or {}).get(SIMPLE_MODES_KEY)
    if not isinstance(table, dict) or mode is None:
        return None
    spec = table.get(mode)
    return spec if isinstance(spec, dict) else None


def is_director_backed(capabilities: Optional[Dict[str, Any]], mode: Optional[str]) -> bool:
    return mode == DIRECTOR_MODE or simple_mode_spec(capabilities, mode) is not None


def simple_mode_problems(table: Any) -> List[str]:
    if not isinstance(table, dict):
        return [f"{SIMPLE_MODES_KEY} must be a mapping of mode id to its spec"]
    problems: List[str] = []
    for mode, spec in table.items():
        if not isinstance(spec, dict):
            problems.append(f"{SIMPLE_MODES_KEY}.{mode} must be a mapping")
            continue
        composition = spec.get("composition")
        if composition not in _COMPOSITIONS:
            problems.append(f"{SIMPLE_MODES_KEY}.{mode}.composition must be one of {sorted(_COMPOSITIONS)}")
            continue
        needs_first, needs_last = _COMPOSITIONS[composition]
        for key, needed in (("first", needs_first), ("last", needs_last)):
            value = spec.get(key)
            if needed and not (isinstance(value, str) and value):
                problems.append(f"{SIMPLE_MODES_KEY}.{mode}.{key} must name the picture field for a {composition} document")
            if not needed and value is not None:
                problems.append(f"{SIMPLE_MODES_KEY}.{mode}.{key} is not used by a {composition} document")
        if not isinstance(spec.get("duration_field", "duration"), str):
            problems.append(f"{SIMPLE_MODES_KEY}.{mode}.duration_field must be a field name")
    return problems


def _media_ref(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value if (value.get("path") or value.get("relative_path")) else None
    if isinstance(value, str) and value:
        return {"path": value, "type": "image"}
    return None


def build_simple_mode_document(
    spec: Dict[str, Any],
    capabilities: Optional[Dict[str, Any]],
    form_data: Dict[str, Any],
    positive_prompt: str,
    negative_prompt: str = "",
    *,
    placeholders: bool = False,
) -> Dict[str, Any]:
    composition = spec["composition"]
    needs_first, needs_last = _COMPOSITIONS[composition]
    limits = (capabilities or {}).get("limits") or {}

    errors: List[str] = []
    duration = form_data.get(spec.get("duration_field", "duration"))
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        if placeholders:
            duration = limits.get("default_duration", 5)
        else:
            errors.append(f"{spec.get('duration_field', 'duration')}: a length in seconds is required")
            duration = 0

    pictures: Dict[str, Dict[str, Any]] = {}
    for role, needed in (("first", needs_first), ("last", needs_last)):
        if not needed:
            continue
        field = spec[role]
        ref = _media_ref(form_data.get(field))
        if ref is None and placeholders:
            ref = {"path": placeholder_name(field), "type": "image"}
        if ref is None:
            errors.append(f"{field}: a picture is required")
        else:
            pictures[role] = ref
    if errors:
        raise VideoDirectorValidationError(errors)

    media: List[Dict[str, Any]] = []
    if "first" in pictures:
        media.append({
            "id": "m-1", "role": "first", "segment_id": "seg-1", "at": 0, "strength": 1, "media": pictures["first"],
        })
    if "last" in pictures:
        media.append({
            "id": f"m-{len(media) + 1}", "role": "last", "segment_id": "seg-1", "at": duration, "strength": 1,
            "media": pictures["last"],
        })

    return {
        "schema_version": 1,
        "mode": composition,
        "settings": {"fps": limits.get("default_fps", 24), "duration": duration, "seed": -1},
        "segments": [{
            "id": "seg-1",
            "prompt": positive_prompt or "",
            "negative_prompt": negative_prompt or "",
            "start": 0,
            "end": duration,
            "frames": None,
            "seed": None,
            "steps": None,
            "cfg": None,
            "loras": None,
        }],
        "media": media,
        "audio": [],
        "ic_lora": [],
    }
