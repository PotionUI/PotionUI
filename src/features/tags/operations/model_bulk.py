from typing import Any, Dict, List

from src.features.tags.repository import TagRepository


def _clean(refs: List[str]) -> List[str]:
    seen: Dict[str, str] = {}
    for ref in refs:
        value = (ref or "").strip()
        if value and value.lower() not in seen:
            seen[value.lower()] = value
    return list(seen.values())


def bulk_update_model_tags(
    tag_repository: TagRepository, model_ids: List[str], add: List[str], remove: List[str]
) -> Dict[str, Any]:
    ids = [model_id for model_id in dict.fromkeys(model_ids) if model_id]
    if not ids:
        raise ValueError("No models selected")
    to_add = _clean(add)
    to_remove = _clean(remove)
    if not to_add and not to_remove:
        raise ValueError("Nothing to add or remove")
    overlap = {ref.lower() for ref in to_add} & {ref.lower() for ref in to_remove}
    if overlap:
        raise ValueError(f"A tag can't be both added and removed: {', '.join(sorted(overlap))}")
    return tag_repository.bulk_update_model_tags(ids, to_add, to_remove)


def model_selection_tags(tag_repository: TagRepository, model_ids: List[str]) -> Dict[str, Any]:
    ids = [model_id for model_id in dict.fromkeys(model_ids) if model_id]
    counts: Dict[str, Dict[str, Any]] = {}
    for tags in tag_repository.get_model_tags_bulk(ids).values():
        for tag in tags:
            item = counts.setdefault(tag.id, {"id": tag.id, "name": tag.name, "count": 0})
            item["count"] += 1
    ordered = sorted(counts.values(), key=lambda item: (-item["count"], item["name"].lower()))
    return {"models": len(ids), "tags": ordered}
