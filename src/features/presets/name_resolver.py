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
        self.preset_loader._ensure_loaded()
        return {preset.id: preset.name for preset in self.preset_loader.presets}

    def resolve(self, preset_id: Optional[str], default: Optional[str] = None) -> Optional[str]:
        if not preset_id:
            return default
        return self.name_map().get(preset_id, default if default is not None else preset_id)
