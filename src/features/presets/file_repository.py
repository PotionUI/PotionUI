from typing import Dict, Any, Optional, List
from src.features.presets import PresetTemplateLoader
from src.features.presets.dto import PresetInfo, PresetStyle

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

    def preset_to_info(
        self, preset_template, include_gallery: bool = False, include_styles: bool = False
    ) -> PresetInfo:
        """
        Convert a preset template to a PresetInfo object.

        Args:
            preset_template: The preset template to convert
            include_gallery: Whether to include the full `media.gallery` list.
                Defaults to False so the list endpoint stays cover-only; the
                detail endpoint (`operations.get_preset`) passes True.
                `src` values are emitted raw/relative - the frontend composes URLs.
            include_styles: Whether to include the full `styles` list, same
                rationale as `include_gallery` (styles.yml can carry dozens
                of entries with long prompts) - the list endpoint stays [],
                the detail endpoint passes True.

        Returns:
            A PresetInfo object with the preset's data
        """
        base_path = preset_template.base_path or ""
        source = "custom" if "presets/local" in base_path else "official"

        media = preset_template.media
        if media and not include_gallery:
            media = {k: v for k, v in media.items() if k != "gallery"}

        # A style's `example_prompt` is optional in styles.yml (the top-level
        # `preview:` block can supply a shared default - see
        # `StylesPreviewDefaults`); resolve it here so the DTO field stays a
        # plain, always-present string for the frontend.
        default_example_prompt = (preset_template.styles_preview or {}).get("example_prompt", "")
        styles = (
            [
                PresetStyle(**{**style, "example_prompt": style.get("example_prompt") or default_example_prompt})
                for style in preset_template.styles
            ]
            if include_styles
            else []
        )

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
            styles=styles,
            vars=preset_template.vars or {},
            llm=preset_template.llm or {},
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


