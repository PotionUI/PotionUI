import copy
from typing import Any, Dict, Mapping, Optional, TypeVar

from src.features.generation.dto import GenerationRequest

BINDABLE_ROLES = ("first", "last")
SINGLE_SHOT_MODES_FOR_ROLE = {"first": ("i2v", "flf"), "last": ("flf",)}

RequestLike = TypeVar("RequestLike", GenerationRequest, Dict[str, Any])


class DirectorBindingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _media_value(media: Mapping[str, Any]) -> Dict[str, Any]:
    value = media.get("value")
    if not isinstance(value, str) or not value:
        raise DirectorBindingError("invalid_media", "The media to bind has no stored path")
    media_type = str(media.get("media_type") or "").lower()
    if media_type != "image":
        raise DirectorBindingError("invalid_media", "Only an image can be a Video Director first or last frame")
    return {"path": value, "relative_path": value, "type": "image"}


def _bind(document: Mapping[str, Any], role: str, media: Mapping[str, Any], segment_id: Optional[str]) -> Dict[str, Any]:
    if role not in BINDABLE_ROLES:
        raise DirectorBindingError("invalid_role", f"Only {' and '.join(BINDABLE_ROLES)} frames can be bound")
    mode = document.get("mode")
    if mode not in SINGLE_SHOT_MODES_FOR_ROLE[role]:
        allowed = " or ".join(SINGLE_SHOT_MODES_FOR_ROLE[role])
        raise DirectorBindingError(
            "unsupported_mode",
            f"A {role} frame can only be bound to a single-shot {allowed} Video Director document, not {mode!r}",
        )
    segments = [segment for segment in document.get("segments") or [] if isinstance(segment, Mapping)]
    if segment_id is None:
        if len(segments) != 1:
            raise DirectorBindingError("segment_required", "Name the segment to bind; the document has more than one")
        segment_id = segments[0].get("id")
    elif not any(segment.get("id") == segment_id for segment in segments):
        raise DirectorBindingError("unknown_segment", f"The document has no segment {segment_id!r}")
    value = _media_value(media)
    bound = dict(document)
    entries = [entry for entry in bound.get("media") or [] if isinstance(entry, dict)]
    existing = next(
        (entry for entry in entries if entry.get("role") == role and entry.get("segment_id") == segment_id),
        None,
    )
    if existing is not None:
        existing["media"] = value
    else:
        entries.append({
            "id": f"bound-{role}-{segment_id}",
            "role": role,
            "segment_id": segment_id,
            "at": 0 if role == "first" else None,
            "strength": 1.0,
            "media": value,
        })
    bound["media"] = entries
    return bound


def bind_director_media(
    request: RequestLike,
    role: str,
    media: Mapping[str, Any],
    segment_id: Optional[str] = None,
) -> RequestLike:
    is_model = isinstance(request, GenerationRequest)
    data = request.model_dump() if is_model else copy.deepcopy(dict(request))
    form_data = dict(data.get("form_data") or {})
    document = form_data.get("video_director")
    if not isinstance(document, Mapping):
        raise DirectorBindingError("no_director", "The request has no Video Director document to bind into")
    form_data["video_director"] = _bind(document, role, media, segment_id)
    data["form_data"] = form_data
    return GenerationRequest.model_validate(data) if is_model else data
