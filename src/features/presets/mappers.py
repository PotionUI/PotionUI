from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.features.presets.dto import PresetInfo
from src.features.presets.mode_labels import mode_display_icon, mode_display_name
from src.features.presets.overlays import PresetOverlays, RowOverlay
from src.features.presets.records import Preset, PresetTombstone
from src.features.presets.templates import PresetTemplate

Counts = Tuple[str, int, int]


def _row_order(row: Preset) -> Tuple[str, str]:
    return (str(row.installed_at or ""), row.id or "")


def row_overlay(row: Preset) -> RowOverlay:
    return RowOverlay(
        preset_id=row.preset_id,
        source_preset_id=row.source_preset_id,
        display_name=row.display_name,
        display_description=row.display_description,
        display_cover=row.display_cover,
        offered_modes=tuple(row.offered_modes) if row.offered_modes is not None else None,
        first_mode=row.first_mode,
    )


def overlays_from_records(rows: Iterable[Preset], tombstones: Iterable[PresetTombstone]) -> PresetOverlays:
    return PresetOverlays(
        rows={row.preset_id: row_overlay(row) for row in sorted(rows, key=_row_order)},
        tombstones={tombstone.preset_id: tombstone.name for tombstone in tombstones},
    )


def read_overlays(repository: Any) -> PresetOverlays:
    return overlays_from_records(repository.get_all_installed_presets(), repository.tombstones())


def _file_values(template: Optional[PresetTemplate]) -> Optional[Dict[str, Optional[str]]]:
    if template is None:
        return None
    return {
        "name": template.name,
        "description": template.description,
        "cover": (template.media or {}).get("cover"),
    }


def _display_values(row: Preset) -> Dict[str, Optional[str]]:
    return {"name": row.display_name, "description": row.display_description, "cover": row.display_cover}


def _effective(display: Dict[str, Optional[str]], file: Optional[Dict[str, Optional[str]]]) -> Dict[str, Optional[str]]:
    return {key: display[key] or (file or {}).get(key) for key in ("name", "description", "cover")}


def _available_modes(template: Optional[PresetTemplate]) -> List[Dict[str, Any]]:
    if template is None:
        return []
    return [
        {"name": name, "label": mode_display_name(name, mode.mode_label), "icon": mode_display_icon(name, mode.mode_icon)}
        for name, mode in template.modes.items()
    ]


def mirror_entry(row: Preset, state: Optional[str]) -> Dict[str, Any]:
    return {"preset_id": row.preset_id, "name": row.display_name or row.preset_id, "mirror_state": state}


def admin_detail(
    row: Preset,
    file_template: Optional[PresetTemplate],
    source_name: Optional[str],
    mirror_state: Optional[str],
    mirrors: List[Dict[str, Any]],
    counts: Optional[Counts],
) -> Dict[str, Any]:
    file = _file_values(file_template)
    display = _display_values(row)
    return {
        "preset_id": row.preset_id,
        "preset_db_id": row.id,
        "is_mirror": row.is_mirror,
        "source_preset_id": row.source_preset_id,
        "source_name": source_name if row.is_mirror else None,
        "mirror_state": mirror_state if row.is_mirror else None,
        "file": file,
        "display": display,
        "effective": _effective(display, file),
        "modes": {
            "available": _available_modes(file_template),
            "offered": list(row.offered_modes) if row.is_mirror and row.offered_modes is not None else None,
            "first": row.first_mode if row.is_mirror else None,
        },
        "mirrors": [] if row.is_mirror else mirrors,
        "assignment_count": counts[1] if counts else 0,
        "group_count": counts[2] if counts else 0,
    }


def admin_list_fields(
    template: PresetTemplate, row: Optional[Preset], mirror_state: Optional[str], mirror_count: int
) -> Dict[str, Any]:
    return {
        "file_name": template.file_name or template.name,
        "display_overridden": bool(row is not None and row.has_display),
        "is_mirror": template.source_preset_id is not None,
        "source_preset_id": template.source_preset_id,
        "mirror_state": mirror_state,
        "mirror_count": mirror_count,
    }


def missing_mirror_entry(row: Preset, mirror_state: str, counts: Optional[Counts]) -> Dict[str, Any]:
    info = PresetInfo(id=row.preset_id, name=row.display_name or row.preset_id, version="").model_dump()
    return {
        **info,
        "installed": True,
        "preset_db_id": row.id,
        "assignment_count": counts[1] if counts else 0,
        "group_count": counts[2] if counts else 0,
        "requirements_summary": None,
        "file_name": "",
        "display_overridden": row.has_display,
        "is_mirror": True,
        "source_preset_id": row.source_preset_id,
        "mirror_state": mirror_state,
        "mirror_count": 0,
    }
