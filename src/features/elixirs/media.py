from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from src.features.elixirs.errors import MediaRefError
from src.features.elixirs.identity import asset_key, media_ref

Resolver = Callable[[Any, Dict[str, str]], Mapping[str, Any]]


class AssetRejected(ValueError):
    def __init__(self, index: int, code: str, message: str):
        super().__init__(message)
        self.index = index
        self.code = code


def normalize_media_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def preview_url(ref: Mapping[str, str], resolved: Mapping[str, Any]) -> Optional[str]:
    if ref["kind"] == "history":
        return f"/api/media/files/{quote(ref['file_id'], safe='')}?size=medium"
    value = resolved.get("value")
    if isinstance(value, str) and value.startswith("uploads/"):
        return f"/api/media/uploads/{quote(value[len('uploads/'):], safe='')}?size=medium"
    return None


def dimensions(source: Mapping[str, Any]) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for key in ("width", "height"):
        value = source.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            found[key] = value
    return found if len(found) == 2 else {}


def resolve_assets(
    user: Any,
    assets: Sequence[Mapping[str, Any]],
    known: Mapping[str, Mapping[str, Any]],
    resolver: Resolver,
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen = set()
    for index, asset in enumerate(assets):
        ref = media_ref(asset)
        key = asset_key(ref)
        if key in seen:
            raise AssetRejected(index, "duplicate_media", f"File {index + 1} is already in this media source.")
        seen.add(key)
        if key in known:
            stored = known[key]
            entries.append({
                **ref,
                "media_type": stored.get("media_type"),
                "original_filename": stored.get("original_filename"),
                "preview_url": stored.get("preview_url"),
                **dimensions(stored),
            })
            continue
        try:
            resolved = resolver(user, ref)
        except MediaRefError:
            raise AssetRejected(index, "unavailable_media", f"File {index + 1} is not available.") from None
        entries.append({
            **ref,
            "media_type": normalize_media_type(resolved.get("media_type")),
            "original_filename": resolved.get("original_filename"),
            "preview_url": preview_url(ref, resolved),
            **dimensions(resolved),
        })
    return entries


def unavailable_assets(
    user: Any,
    nodes: Sequence[Mapping[str, Any]],
    resolver: Resolver,
) -> Dict[str, List[Tuple[int, str]]]:
    missing: Dict[str, List[Tuple[int, str]]] = {}
    for node in nodes:
        if node.get("kind") != "media":
            continue
        for index, asset in enumerate(node.get("assets") or []):
            try:
                resolver(user, media_ref(asset))
            except MediaRefError as error:
                reason = "it was deleted or you cannot open it" if error.code == "not_found" else "the reference is not valid"
                missing.setdefault(node["id"], []).append((index, reason))
    return missing
