import copy
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple, TypeVar

from src.features.generation.dto import GenerationRequest
from src.features.prompt.markers import RESOURCE_MARKER_RE
from src.features.prompt.resources import media_item_keys

BINDABLE_ROLES = ("first", "last")
DIRECTOR_ROLES = ("first", "last", "keyframe")
REFERENCE_ROLE = "reference"
REFERENCE_MEDIA_TYPES = ("image", "video", "audio")
UNCITABLE_CHARACTERS = ("]", "\r", "\n")
SINGLE_SHOT_MODES_FOR_ROLE = {"first": ("i2v", "flf"), "last": ("flf",)}
PROMOTED_MODE_FOR_ROLE = {"first": {"t2v": "i2v"}, "last": {"i2v": "flf"}}
DEFAULT_FPS = 24
DEFAULT_MAX_KEYFRAMES = 8
AT_TOLERANCE = 1e-6

RequestLike = TypeVar("RequestLike", GenerationRequest, Dict[str, Any])


class DirectorBindingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _media_value(media: Mapping[str, Any]) -> Dict[str, Any]:
    value = media.get("value")
    if not isinstance(value, str) or not value:
        raise DirectorBindingError("invalid_media", "The media to bind has no stored path")
    media_type = str(media.get("media_type") or "").lower()
    if media_type != "image":
        raise DirectorBindingError("invalid_media", "Only an image can be a Video Director frame")
    return {"path": value, "relative_path": value, "type": "image"}


def _segments(document: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [segment for segment in document.get("segments") or [] if isinstance(segment, Mapping)]


def _entries(document: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [entry for entry in document.get("media") or [] if isinstance(entry, dict)]


def _bind(
    document: Mapping[str, Any],
    role: str,
    media: Mapping[str, Any],
    segment_id: Optional[str],
    promote: bool = False,
) -> Dict[str, Any]:
    if role not in BINDABLE_ROLES:
        raise DirectorBindingError("invalid_role", f"Only {' and '.join(BINDABLE_ROLES)} frames can be bound")
    mode = document.get("mode")
    promoted = PROMOTED_MODE_FOR_ROLE[role].get(mode) if promote else None
    if mode not in SINGLE_SHOT_MODES_FOR_ROLE[role] and promoted is None:
        allowed = " or ".join(SINGLE_SHOT_MODES_FOR_ROLE[role])
        raise DirectorBindingError(
            "unsupported_mode",
            f"A {role} frame can only be bound to a single-shot {allowed} Video Director document, not {mode!r}",
        )
    segments = _segments(document)
    if segment_id is None:
        if len(segments) != 1:
            raise DirectorBindingError("segment_required", "Name the segment to bind; the document has more than one")
        segment_id = segments[0].get("id")
    elif not any(segment.get("id") == segment_id for segment in segments):
        raise DirectorBindingError("unknown_segment", f"The document has no segment {segment_id!r}")
    value = _media_value(media)
    bound = dict(document)
    entries = _entries(bound)
    if promoted == "flf" and not any(
        entry.get("role") == "first" and entry.get("segment_id") == segment_id for entry in entries
    ):
        raise DirectorBindingError("first_frame_required", "A last frame needs a first frame on the same shot")
    existing = next(
        (entry for entry in entries if entry.get("role") == role and entry.get("segment_id") == segment_id),
        None,
    )
    if existing is not None:
        existing["media"] = value
    else:
        duration = (bound.get("settings") or {}).get("duration")
        entries.append({
            "id": f"bound-{role}-{segment_id}",
            "role": role,
            "segment_id": segment_id,
            "at": 0 if role == "first" else (duration if isinstance(duration, (int, float)) else None),
            "strength": 1.0,
            "media": value,
        })
    bound["media"] = entries
    if promoted is not None:
        bound["mode"] = promoted
    return bound


def _director_modes(capabilities: Mapping[str, Any]) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
    modes = capabilities.get("modes")
    modes = modes if isinstance(modes, Mapping) else {}
    if "director" not in modes:
        raise DirectorBindingError("unsupported_mode", "The preset mode has no director composition mode")
    director = modes.get("director")
    return modes, director if isinstance(director, Mapping) else {}


def _segment_window(
    document: Mapping[str, Any],
    capabilities: Mapping[str, Any],
    segments: List[Mapping[str, Any]],
    index: int,
    chain: bool,
) -> Tuple[Optional[float], Optional[float]]:
    if not chain:
        start, end = segments[index].get("start"), segments[index].get("end")
        if _is_number(start) and _is_number(end) and end > start:
            return float(start), float(end)
        return None, None
    fps = (document.get("settings") or {}).get("fps") or (capabilities.get("limits") or {}).get("default_fps") or DEFAULT_FPS
    frames = [segment.get("frames") for segment in segments[: index + 1]]
    if not _is_number(fps) or fps <= 0 or not all(isinstance(count, int) and not isinstance(count, bool) and count > 0 for count in frames):
        return None, None
    start = sum(frames[:-1]) / fps
    return start, start + frames[-1] / fps


def _place_edge(
    entries: List[Dict[str, Any]],
    role: str,
    segment: Mapping[str, Any],
    chain: bool,
    window: Tuple[Optional[float], Optional[float]],
    value: Dict[str, Any],
) -> None:
    segment_id = segment.get("id")
    if chain and role == "first" and segment.get("sub_type") == "chain":
        raise DirectorBindingError(
            "role_unsupported", f"Segment {segment_id!r} continues the previous one, so it cannot open on its own frame",
        )
    if chain and role == "last" and not any(
        entry.get("role") == "first" and entry.get("segment_id") == segment_id for entry in entries
    ):
        raise DirectorBindingError("first_frame_required", "A last frame needs a first frame on the same segment")
    existing = next(
        (entry for entry in entries if entry.get("role") == role and entry.get("segment_id") == segment_id),
        None,
    )
    if existing is not None:
        existing["media"] = value
        return
    entries.append({
        "id": f"bound-{role}-{segment_id}",
        "role": role,
        "segment_id": segment_id,
        "at": window[0] if role == "first" else window[1],
        "strength": 1.0,
        "media": value,
    })


def _place_keyframe(
    entries: List[Dict[str, Any]],
    segment_id: str,
    at: Any,
    window: Tuple[Optional[float], Optional[float]],
    mode_caps: Mapping[str, Any],
    value: Dict[str, Any],
) -> None:
    start, end = window
    if not _is_number(at) or at < 0:
        raise DirectorBindingError("invalid_at", "A keyframe needs at, in seconds from the start of its segment")
    if start is None or end is None:
        raise DirectorBindingError("invalid_at", f"Segment {segment_id!r} has no timing to place a keyframe in")
    length = end - start
    if at > length + AT_TOLERANCE:
        raise DirectorBindingError("invalid_at", f"at {at:g} is past the end of segment {segment_id!r} ({length:g}s)")
    placed = round(start + min(float(at), length), 6)
    keyframes = [entry for entry in entries if entry.get("role") == "keyframe"]
    existing = next(
        (entry for entry in keyframes if _is_number(entry.get("at")) and abs(entry["at"] - placed) <= AT_TOLERANCE),
        None,
    )
    if existing is not None:
        existing["media"] = value
        return
    cap = mode_caps.get("max_keyframes", DEFAULT_MAX_KEYFRAMES)
    if isinstance(cap, int) and len(keyframes) >= cap:
        raise DirectorBindingError("too_many_keyframes", f"The director mode takes at most {cap} keyframes")
    entries.append({
        "id": f"bound-keyframe-{segment_id}-{int(round(placed * 1000))}",
        "role": "keyframe",
        "segment_id": None,
        "at": placed,
        "strength": 1.0,
        "media": value,
    })


def _bind_director(
    document: Mapping[str, Any],
    role: str,
    media: Mapping[str, Any],
    segment_id: Optional[str],
    at: Any,
    capabilities: Mapping[str, Any],
) -> Dict[str, Any]:
    if role not in DIRECTOR_ROLES:
        raise DirectorBindingError("invalid_role", f"Only {', '.join(DIRECTOR_ROLES)} media can be bound to a director document")
    if segment_id is None:
        raise DirectorBindingError("segment_required", "Name the segment to bind; a director document needs one")
    segments = _segments(document)
    index = next((i for i, segment in enumerate(segments) if segment.get("id") == segment_id), None)
    if index is None:
        raise DirectorBindingError("unknown_segment", f"The document has no segment {segment_id!r}")
    modes, mode_caps = _director_modes(capabilities)
    chain = bool(capabilities.get("segment_routing"))
    keyframes = mode_caps.get("keyframes")
    anywhere = not chain or keyframes == "anywhere"
    allowed = {
        "first": "i2v" in modes or "flf" in modes or anywhere or keyframes == "first_only",
        "last": "flf" in modes or anywhere,
        "keyframe": anywhere,
    }
    if not allowed[role]:
        raise DirectorBindingError("role_unsupported", f"This preset's director mode takes no {role} media")
    if role != "keyframe" and at is not None:
        raise DirectorBindingError("invalid_at", "at only places a keyframe")
    value = _media_value(media)
    window = _segment_window(document, capabilities, segments, index, chain)
    bound = dict(document)
    entries = _entries(bound)
    if role == "keyframe":
        _place_keyframe(entries, segment_id, at, window, mode_caps, value)
    else:
        _place_edge(entries, role, segments[index], chain, window, value)
    bound["media"] = entries
    return bound


def _reference_items(media: Any) -> List[Dict[str, Any]]:
    items = list(media) if isinstance(media, (list, tuple)) else [media]
    if not items:
        raise DirectorBindingError("invalid_media", "Bind at least one reference")
    out = []
    for item in items:
        value = item.get("value") if isinstance(item, Mapping) else None
        if not isinstance(value, str) or not value:
            raise DirectorBindingError("invalid_media", "The media to bind has no stored path")
        kind = str(item.get("media_type") or "").lower()
        if kind not in REFERENCE_MEDIA_TYPES:
            raise DirectorBindingError("invalid_media", "A reference must be an image, a video or an audio track")
        entry = {"path": value, "relative_path": value, "type": kind}
        if item.get("original_filename"):
            entry["name"] = item["original_filename"]
        out.append(entry)
    return out


def _cite(document: Mapping[str, Any], segment_id: str, field: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    bound = dict(document)
    segments = [dict(segment) if isinstance(segment, Mapping) else segment for segment in bound.get("segments") or []]
    target = next((s for s in segments if isinstance(s, dict) and s.get("id") == segment_id), None)
    if target is None:
        raise DirectorBindingError("unknown_segment", f"The document has no segment {segment_id!r}")
    if any(character in item["relative_path"] for item in items for character in UNCITABLE_CHARACTERS):
        raise DirectorBindingError("invalid_media", "A reference whose path holds a ] or a line break cannot be cited in a prompt")
    prompt = target.get("prompt") if isinstance(target.get("prompt"), str) else ""
    cited = {(match.group(1), match.group(2)) for match in RESOURCE_MARKER_RE.finditer(prompt)}
    markers = []
    for item in items:
        if (field, item["relative_path"]) not in cited:
            cited.add((field, item["relative_path"]))
            markers.append(f"@[{field}:{item['relative_path']}]")
    target["prompt"] = " ".join(part for part in [prompt.rstrip(), *markers] if part)
    bound["segments"] = segments
    return bound


def _bind_references(
    form_data: Dict[str, Any],
    document: Mapping[str, Any],
    media: Any,
    segment_id: Optional[str],
    capabilities: Optional[Mapping[str, Any]],
    field: Optional[str],
) -> Mapping[str, Any]:
    caps = capabilities if isinstance(capabilities, Mapping) else {}
    fields = [name for name in caps.get("reference_fields") or [] if isinstance(name, str) and name]
    if not caps.get("references") or not fields:
        raise DirectorBindingError("references_unsupported", "The preset mode has no reference pool to bind into")
    field = fields[0] if field is None else field
    if field not in fields:
        raise DirectorBindingError("unknown_field", f"{field!r} is not one of the reference fields ({', '.join(fields)})")
    items = _reference_items(media)
    if segment_id is not None and (document.get("mode") != "director" or caps.get("references") != "per_shot"):
        raise DirectorBindingError(
            "unsupported_mode", "Only a director document with per-shot references cites references per segment",
        )
    cited = document if segment_id is None else _cite(document, segment_id, field, items)
    raw = form_data.get(field)
    pool = list(raw) if isinstance(raw, list) else ([] if raw is None or raw == "" else [raw])
    known = {key for entry in pool for key in media_item_keys(entry)}
    for item in items:
        if item["relative_path"] not in known:
            known.add(item["relative_path"])
            pool.append(item)
    form_data[field] = pool
    return cited


def bind_director_media(
    request: RequestLike,
    role: str,
    media: Any,
    segment_id: Optional[str] = None,
    promote: bool = False,
    *,
    at: Optional[float] = None,
    capabilities: Optional[Mapping[str, Any]] = None,
    field: Optional[str] = None,
) -> RequestLike:
    is_model = isinstance(request, GenerationRequest)
    data = request.model_dump() if is_model else copy.deepcopy(dict(request))
    form_data = dict(data.get("form_data") or {})
    document = form_data.get("video_director")
    if not isinstance(document, Mapping):
        raise DirectorBindingError("no_director", "The request has no Video Director document to bind into")
    if role == REFERENCE_ROLE:
        if at is not None:
            raise DirectorBindingError("invalid_at", "at only places a keyframe")
        form_data["video_director"] = _bind_references(form_data, document, media, segment_id, capabilities, field)
    elif document.get("mode") == "director" and capabilities is not None:
        form_data["video_director"] = _bind_director(document, role, media, segment_id, at, capabilities)
    else:
        if at is not None:
            raise DirectorBindingError("invalid_at", "at only places a keyframe in a director document")
        form_data["video_director"] = _bind(document, role, media, segment_id, promote)
    data["form_data"] = form_data
    return GenerationRequest.model_validate(data) if is_model else data
