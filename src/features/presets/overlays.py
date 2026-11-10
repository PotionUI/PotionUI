from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from src.features.presets.templates import PresetTemplate

MIRROR_OK = "ok"
MIRROR_SOURCE_MISSING = "source_missing"
MIRROR_MODES_MISSING = "modes_missing"
MIRROR_ID_COLLISION = "id_collision"


@dataclass(frozen=True)
class RowOverlay:
    preset_id: str
    source_preset_id: Optional[str] = None
    display_name: Optional[str] = None
    display_description: Optional[str] = None
    display_cover: Optional[str] = None
    offered_modes: Optional[Tuple[str, ...]] = None
    first_mode: Optional[str] = None

    @property
    def is_mirror(self) -> bool:
        return self.source_preset_id is not None

    @property
    def has_display(self) -> bool:
        return any(value is not None for value in (self.display_name, self.display_description, self.display_cover))


@dataclass(frozen=True)
class PresetOverlays:
    rows: Dict[str, RowOverlay] = field(default_factory=dict)
    tombstones: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OverlayResult:
    presets: List[PresetTemplate]
    mirror_states: Dict[str, str]
    retired_names: Dict[str, str]


def _with_cover(media: Optional[dict], cover: Optional[str]) -> Optional[dict]:
    if not cover:
        return media
    return {**(media or {}), "cover": cover}


def _with_display(template: PresetTemplate, row: RowOverlay) -> PresetTemplate:
    return replace(
        template,
        name=row.display_name or template.name,
        description=row.display_description or template.description,
        media=_with_cover(template.media, row.display_cover),
    )


def mirror_modes(source: PresetTemplate, offered: Optional[Tuple[str, ...]], first: Optional[str]) -> List[str]:
    kept = {mode for mode in (offered or source.modes) if mode in source.modes}
    ordered = [mode for mode in source.modes if mode in kept]
    if first in kept:
        ordered = [first, *(mode for mode in ordered if mode != first)]
    return ordered


def _mirror(source: PresetTemplate, row: RowOverlay, modes: List[str]) -> PresetTemplate:
    return replace(
        source,
        id=row.preset_id,
        name=row.display_name or source.name,
        description=row.display_description or source.description,
        media=_with_cover(source.media, row.display_cover),
        modes={mode: source.modes[mode] for mode in modes},
        requirements=[spec for spec in source.requirements if spec.get("mode") in (None, *modes)],
        source_preset_id=source.id,
        file_name=source.file_name,
    )


def _first_by_id(templates: List[PresetTemplate]) -> Dict[str, PresetTemplate]:
    by_id: Dict[str, PresetTemplate] = {}
    for template in templates:
        by_id.setdefault(template.id, template)
    return by_id


def apply_overlays(
    scanned: List[PresetTemplate], overlays: PresetOverlays, errors: Dict[str, List[str]]
) -> OverlayResult:
    file_rows = {key: row for key, row in overlays.rows.items() if not row.is_mirror and row.has_display}
    presets = [_with_display(t, file_rows[t.id]) if t.id in file_rows else t for t in scanned]
    files = _first_by_id(scanned)
    mirror_states: Dict[str, str] = {}
    retired_names: Dict[str, str] = {}
    for row in overlays.rows.values():
        if not row.is_mirror:
            continue
        if row.preset_id in files:
            mirror_states[row.preset_id] = MIRROR_ID_COLLISION
            errors.setdefault(f"mirror:{row.preset_id}", []).append(
                f"a preset file declares the id '{row.preset_id}', so this mirror is hidden"
            )
            continue
        source = files.get(row.source_preset_id)
        if source is None:
            mirror_states[row.preset_id] = MIRROR_SOURCE_MISSING
            retired_names[row.preset_id] = row.display_name or row.preset_id
            continue
        modes = mirror_modes(source, row.offered_modes, row.first_mode)
        if not modes:
            mirror_states[row.preset_id] = MIRROR_MODES_MISSING
            retired_names[row.preset_id] = row.display_name or row.preset_id
            continue
        presets.append(_mirror(source, row, modes))
        mirror_states[row.preset_id] = MIRROR_OK
    for preset_id, name in overlays.tombstones.items():
        retired_names.setdefault(preset_id, name)
    return OverlayResult(presets=presets, mirror_states=mirror_states, retired_names=retired_names)
