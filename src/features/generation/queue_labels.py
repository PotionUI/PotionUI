from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from src.features.generation.origin import KeyNameLookup, present_origins


def label_queue(
    snapshot: Dict[str, List[Dict[str, Any]]],
    user_id: str,
    rows_for: Callable[[str, List[str]], Mapping[str, Dict[str, Any]]],
    preset_names: Mapping[str, str],
    key_names: Optional[KeyNameLookup],
) -> Dict[str, List[Dict[str, Any]]]:
    pending = [dict(entry) for entry in snapshot.get("pending", [])]
    running = [dict(entry) for entry in snapshot.get("running", [])]
    rows = rows_for(user_id, [entry["generation_id"] for entry in pending + running])
    for status, entries in (("pending", pending), ("running", running)):
        for entry in entries:
            row = rows.get(entry["generation_id"], {})
            preset_id = entry.get("preset_id") or row.get("preset_id")
            entry["status"] = status
            entry["preset_id"] = preset_id
            entry["preset_name"] = preset_names.get(preset_id, preset_id) if preset_id else None
            entry["mode"] = row.get("mode")
            entry["form_name"] = row.get("form_name")
            entry["has_tab"] = bool(entry.get("tab_id"))
            entry["created_at"] = row.get("created_at")
            entry["started_at"] = row.get("started_at")
            entry["origin"] = row.get("origin")
    present_origins(pending + running, key_names, owner_id=user_id)
    return {**snapshot, "pending": pending, "running": running}


def label_active(
    entries: List[Tuple[Optional[str], Dict[str, Any]]],
    rows_for: Callable[[str, List[str]], Mapping[str, Dict[str, Any]]],
    key_names: Optional[KeyNameLookup],
) -> List[Dict[str, Any]]:
    by_owner: Dict[str, List[Dict[str, Any]]] = {}
    for owner, item in entries:
        item["origin"] = None
        if owner:
            by_owner.setdefault(owner, []).append(item)
    for owner, items in by_owner.items():
        rows = rows_for(owner, [item["id"] for item in items])
        for item in items:
            item["origin"] = rows.get(item["id"], {}).get("origin")
        present_origins(items, key_names, owner_id=owner)
    return [item for _, item in entries]
