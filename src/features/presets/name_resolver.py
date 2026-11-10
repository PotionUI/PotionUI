"""Resolve preset ids to their display names."""

from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.features.presets.loader import PresetTemplateLoader


class PresetNameResolver:
    """Maps preset ids (ULIDs) to the ``name:`` declared in their preset.yml."""

    def __init__(self, preset_loader: "PresetTemplateLoader"):
        self.preset_loader = preset_loader

    def name_map(self) -> Dict[str, str]:
        """Snapshot of every loaded preset's id -> name.

        Callers resolving many ids should take one snapshot and read it,
        rather than calling ``resolve`` per id.
        """
        names: Dict[str, str] = {}
        for preset in self.preset_loader.all():
            names.setdefault(preset.id, preset.name)
        for preset_id, name in self.preset_loader.retired_names.items():
            names.setdefault(preset_id, name)
        return names

    def resolve(self, preset_id: Optional[str], default: Optional[str] = None) -> Optional[str]:
        if not preset_id:
            return default
        preset = self.preset_loader.get(preset_id)
        if preset is not None:
            return preset.name
        retired = self.preset_loader.retired_name(preset_id)
        if retired is not None:
            return retired
        return default if default is not None else preset_id
