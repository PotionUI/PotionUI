from typing import Any, Dict, List, Optional

from src.features.kits import citations
from src.features.kits.catalog import ItemView, KitView
from src.features.kits.records import Kit
from src.features.media.records import Upload
from src.platform.database.rows import dt_iso


def media_to_dict(upload: Upload) -> Dict[str, Any]:
    base = f"/api/media/uploads/{upload.filename}"
    return {
        "upload_id": upload.id,
        "filename": upload.filename,
        "original_filename": upload.original_filename,
        "media_type": upload.media_type,
        "url": base,
        "thumbnail_small": f"{base}?size=small" if upload.thumbnail_small else None,
        "thumbnail_medium": f"{base}?size=medium" if upload.thumbnail_medium else None,
        "width": upload.width,
        "height": upload.height,
        "duration_seconds": upload.duration_seconds,
        "size": upload.file_size,
    }


def item_to_dict(view: ItemView) -> Dict[str, Any]:
    item, kit = view.item, view.kit
    return {
        "id": item.id,
        "kit_id": kit.id,
        "name": item.name,
        "kind": item.kind,
        "position": item.position,
        "path": f"kits.{kit.handle}.{item.name}",
        "ref": citations.item_key(kit.id, item.id),
        "text": item.text if item.is_text else None,
        "media": media_to_dict(view.upload) if view.upload is not None else None,
        "missing": view.missing,
        "created_at": dt_iso(item.created_at),
    }


def kit_to_dict(kit: Kit) -> Dict[str, Any]:
    return {
        "id": kit.id,
        "name": kit.name,
        "handle": kit.handle,
        "label": kit.label,
        "position": kit.position,
        "path": f"kits.{kit.handle}",
        "ref": citations.item_key(kit.id),
        "created_at": dt_iso(kit.created_at),
        "updated_at": dt_iso(kit.updated_at),
    }


def kit_with_items(view: KitView) -> Dict[str, Any]:
    counts = {"image": 0, "video": 0, "audio": 0, "text": 0}
    for item in view.items:
        counts[item.item.kind] += 1
    return {**kit_to_dict(view.kit), "counts": counts, "items": [item_to_dict(item) for item in view.items]}


def summary_to_dict(kit: Kit, counts: Dict[str, int], thumbnails: List[dict]) -> Dict[str, Any]:
    return {
        **kit_to_dict(kit),
        "counts": counts,
        "item_count": sum(counts.values()),
        "thumbnails": [
            {"item_id": entry["item_id"], "kind": entry["kind"], **_thumbnail(entry["upload"])} for entry in thumbnails
        ],
    }


def _thumbnail(upload: Upload) -> Dict[str, Optional[str]]:
    media = media_to_dict(upload)
    return {"url": media["url"], "thumbnail_small": media["thumbnail_small"], "thumbnail_medium": media["thumbnail_medium"]}
