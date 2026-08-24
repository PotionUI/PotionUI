from typing import Dict, Any, Optional, List
from src.features.presets import PresetTemplateLoader
from src.features.presets.dto import PresetInfo

class FilePresetRepository:
    """
    Repository class for file-based preset operations.
    Provides utility functions for finding and manipulating presets stored as YAML files.
    """

    def __init__(self, preset_loader: PresetTemplateLoader):
        self.preset_loader = preset_loader

    def find_preset_by_id(self, preset_id: str):
        """
        Find a preset template by its ID.

        Args:
            preset_id: The ID of the preset to find

        Returns:
            The preset template if found, None otherwise
        """
        if not preset_id:
            return None

        # Ensure presets are loaded (lazy loading)
        self.preset_loader._ensure_loaded()

        for preset_template in self.preset_loader.presets:
            if preset_template.id == preset_id:
                return preset_template
        return None

    def preset_to_info(self, preset_template, include_gallery: bool = False) -> PresetInfo:
        """
        Convert a preset template to a PresetInfo object.

        Args:
            preset_template: The preset template to convert
            include_gallery: Whether to include the full `media.gallery` list.
                Defaults to False so the list endpoint stays cover-only; the
                detail endpoint (`PresetManager.get_preset`) passes True.
                `src` values are emitted raw/relative - the frontend composes URLs.

        Returns:
            A PresetInfo object with the preset's data
        """
        base_path = preset_template.base_path or ""
        source = "custom" if "presets/local" in base_path else "official"

        media = preset_template.media
        if media and not include_gallery:
            media = {k: v for k, v in media.items() if k != "gallery"}

        return PresetInfo(
            id=preset_template.id,
            name=preset_template.name,
            version=preset_template.version,
            description=preset_template.description,
            tags=preset_template.tags or [],
            category=preset_template.category,
            source=source,
            engine=preset_template.engine,
            media=media,
            requires=preset_template.requires,
        )

    def list_all_presets(self) -> List[Dict[str, Any]]:
        """
        Get a list of all available presets.

        Returns:
            A list of preset info dictionaries
        """
        # Ensure presets are loaded (lazy loading)
        self.preset_loader._ensure_loaded()

        presets = []
        # The preset_loader.presets is now a simple list
        for preset_template in self.preset_loader.presets:
            preset_info = self.preset_to_info(preset_template)
            presets.append(preset_info.model_dump())
        return presets


