"""Canonical camera-shot taxonomy for the `camera_shot` field.

A structured phrasebook of camera angles, framing distances, subject
orientations, and (video) camera motions. Each shot carries a stable `key`, a
display `label`, and a generic `default_phrase` good enough to use unconfigured.

Per-model wording is stored as a `camera_vocabulary` map (canonical key ->
phrase override) on the model record. `resolve_phrase` / `resolve_catalog`
apply that override on top of the default. The same catalog drives the form
field, the admin editor, and the chat workspace block, so it lives here once.
"""

from typing import Any, Dict, List, Optional


CAMERA_SHOT_CATEGORIES: List[Dict[str, Any]] = [
    {
        "key": "angle",
        "label": "Angle",
        "shots": [
            {"key": "eye_level", "label": "Eye level", "default_phrase": "eye-level shot"},
            {"key": "low_angle", "label": "Low angle", "default_phrase": "low-angle shot, camera looking up"},
            {"key": "high_angle", "label": "High angle", "default_phrase": "high-angle shot, camera looking down"},
            {"key": "overhead", "label": "Overhead", "default_phrase": "overhead shot, viewed from directly above"},
            {"key": "worms_eye", "label": "Worm's-eye", "default_phrase": "worm's-eye view, extreme low angle looking straight up"},
            {"key": "dutch_angle", "label": "Dutch angle", "default_phrase": "dutch angle, tilted camera"},
        ],
    },
    {
        "key": "distance",
        "label": "Distance",
        "shots": [
            {"key": "extreme_close_up", "label": "Extreme close-up", "default_phrase": "extreme close-up"},
            {"key": "close_up", "label": "Close-up", "default_phrase": "close-up shot"},
            {"key": "medium_close_up", "label": "Medium close-up", "default_phrase": "medium close-up, head and shoulders"},
            {"key": "medium", "label": "Medium", "default_phrase": "medium shot, from the waist up"},
            {"key": "cowboy", "label": "Cowboy", "default_phrase": "cowboy shot, framed from mid-thigh up"},
            {"key": "full", "label": "Full", "default_phrase": "full shot, head to toe"},
            {"key": "wide", "label": "Wide", "default_phrase": "wide shot"},
            {"key": "extreme_wide", "label": "Extreme wide", "default_phrase": "extreme wide shot, subject small in the frame"},
        ],
    },
    {
        "key": "orientation",
        "label": "Orientation",
        "shots": [
            {"key": "front", "label": "Front", "default_phrase": "front view, facing the camera"},
            {"key": "three_quarter", "label": "Three-quarter", "default_phrase": "three-quarter view"},
            {"key": "profile", "label": "Profile", "default_phrase": "side profile view"},
            {"key": "back", "label": "Back", "default_phrase": "rear view, seen from behind"},
            {"key": "over_shoulder", "label": "Over-the-shoulder", "default_phrase": "over-the-shoulder shot"},
        ],
    },
    {
        "key": "motion",
        "label": "Camera motion",
        "shots": [
            {"key": "static", "label": "Static", "default_phrase": "static camera, locked-off shot"},
            {"key": "dolly_in", "label": "Dolly in", "default_phrase": "dolly in, camera moving toward the subject"},
            {"key": "dolly_out", "label": "Dolly out", "default_phrase": "dolly out, camera moving away from the subject"},
            {"key": "orbit_left", "label": "Orbit left", "default_phrase": "camera orbiting left around the subject"},
            {"key": "orbit_right", "label": "Orbit right", "default_phrase": "camera orbiting right around the subject"},
            {"key": "pan", "label": "Pan", "default_phrase": "camera panning horizontally"},
            {"key": "tilt", "label": "Tilt", "default_phrase": "camera tilting vertically"},
            {"key": "crane_up", "label": "Crane up", "default_phrase": "crane shot rising upward"},
            {"key": "crane_down", "label": "Crane down", "default_phrase": "crane shot descending downward"},
            {"key": "tracking", "label": "Tracking", "default_phrase": "tracking shot following the subject"},
        ],
    },
]


CATEGORY_KEYS: List[str] = [category["key"] for category in CAMERA_SHOT_CATEGORIES]

# The categories shown when a preset omits `categories`. Motion is opt-in: it
# only makes sense for video presets, which list it explicitly.
DEFAULT_CATEGORY_KEYS: List[str] = ["angle", "distance", "orientation"]


def _shot_index() -> Dict[str, Dict[str, str]]:
    index: Dict[str, Dict[str, str]] = {}
    for category in CAMERA_SHOT_CATEGORIES:
        for shot in category["shots"]:
            index[shot["key"]] = shot
    return index


_SHOTS_BY_KEY = _shot_index()


def valid_shot_keys() -> set:
    """Every canonical shot key across all categories."""
    return set(_SHOTS_BY_KEY.keys())


def default_phrase(shot_key: str) -> Optional[str]:
    """The generic default phrase for a shot key, or None if the key is unknown."""
    shot = _SHOTS_BY_KEY.get(shot_key)
    return shot["default_phrase"] if shot else None


def resolve_phrase(shot_key: str, vocabulary: Optional[Dict[str, Any]]) -> Optional[str]:
    """Resolve a shot's phrase: a non-blank model override wins, else the default.

    Unknown keys resolve to None (no default and no override to fall back on).
    """
    default = default_phrase(shot_key)
    if default is None:
        # Unknown key - no default and nothing to override.
        return None
    if vocabulary:
        override = vocabulary.get(shot_key)
        if isinstance(override, str) and override.strip():
            return override.strip()
    return default


def resolve_catalog(
    vocabulary: Optional[Dict[str, Any]] = None,
    categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Catalog with each shot's resolved phrase, filtered to `categories` if given.

    Each shot gets `key`, `label`, `default_phrase`, the resolved `phrase`
    (override-or-default) and `overridden` (whether a model override applied).
    `categories` filters and orders which category keys are included; unknown
    category keys are ignored, None means all categories in canonical order.
    """
    wanted = None if categories is None else [key for key in categories if key in set(CATEGORY_KEYS)]

    ordered = CATEGORY_KEYS if wanted is None else wanted
    by_key = {category["key"]: category for category in CAMERA_SHOT_CATEGORIES}

    result: List[Dict[str, Any]] = []
    for category_key in ordered:
        category = by_key.get(category_key)
        if not category:
            continue
        shots = []
        for shot in category["shots"]:
            resolved = resolve_phrase(shot["key"], vocabulary)
            override = None
            if vocabulary:
                candidate = vocabulary.get(shot["key"])
                if isinstance(candidate, str) and candidate.strip():
                    override = candidate.strip()
            shots.append({
                "key": shot["key"],
                "label": shot["label"],
                "default_phrase": shot["default_phrase"],
                "phrase": resolved,
                "overridden": override is not None and override != shot["default_phrase"],
            })
        result.append({"key": category["key"], "label": category["label"], "shots": shots})
    return result
