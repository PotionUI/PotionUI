from typing import Any, Iterable, List, Optional

from src.platform.filesystem.audio_formats import AUDIO_EXTENSIONS
from src.platform.filesystem.visual_formats import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

MEDIA_KINDS = ("image", "video", "audio")

_AUDIO_EXTENSIONS = frozenset(AUDIO_EXTENSIONS | {".opus"})

_ITEM_NAME_KEYS = ("name", "relative_path", "path", "url")


def kind_from_declared(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    category = value.strip().lower().split("/", 1)[0]
    return category if category in MEDIA_KINDS else None


def kind_from_filename(value: Any) -> Optional[str]:
    if not isinstance(value, str) or "." not in value:
        return None
    extension = "." + value.split("?", 1)[0].rsplit(".", 1)[-1].lower()
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in _AUDIO_EXTENSIONS:
        return "audio"
    return None


def media_item_kind(item: Any) -> Optional[str]:
    if isinstance(item, str):
        return kind_from_filename(item)
    if not isinstance(item, dict):
        return None
    for key in ("type", "file_type", "media_type"):
        declared = kind_from_declared(item.get(key))
        if declared:
            return declared
    for key in _ITEM_NAME_KEYS:
        kind = kind_from_filename(item.get(key))
        if kind:
            return kind
    return None


def items_of_kind(items: Any, kind: str) -> List[Any]:
    if items is None or items == "":
        return []
    values: Iterable[Any] = items if isinstance(items, (list, tuple)) else [items]
    return [item for item in values if media_item_kind(item) == kind]
