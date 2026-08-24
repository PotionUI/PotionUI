"""
Icon mapping for template processing.

Provides icon name resolution for UI elements.
"""

from typing import Optional


class IconMapper:
    """
    Maps icon types to icon names for UI display.

    Provides predefined mappings for common icon types and allows custom icons.
    """

    # Default icon mappings for common types
    DEFAULT_ICON_MAPPINGS = {
        "prompt": "pencil-square",
        "lora": "puzzle-piece",
        "controlnet": "viewfinder-circle",
        "advanced": "cog-6-tooth",
        "face_detection": "face-smile",
        "input": "photo",
        "enhancement": "sparkles",
        "upscale": "arrows-pointing-out",
        "lighting": "sun",
        "composition": "squares-2x2",
        "style": "paint-brush",
        "quality": "star",
        "model": "cube",
        "settings": "adjustments-horizontal",
        "output": "document-arrow-down",
        "generation": "bolt",
        "processing": "cpu-chip",
        "filters": "funnel",
        "effects": "sparkles",
    }

    def __init__(self, custom_mappings: Optional[dict] = None):
        """
        Initialize IconMapper.

        Args:
            custom_mappings: Optional dictionary of custom icon mappings to add or override defaults.
        """
        self._mappings = self.DEFAULT_ICON_MAPPINGS.copy()
        if custom_mappings:
            self._mappings.update(custom_mappings)

    def get_icon(self, icon_type: str) -> str:
        """
        Get an icon name for the specified type.

        If the icon_type matches a predefined mapping, returns the mapped icon name.
        Otherwise, returns the icon_type as-is (allowing custom icon names).

        Args:
            icon_type: The type of icon needed (e.g., "prompt", "lora").

        Returns:
            The icon name/identifier to be used in the frontend.
        """
        return self._mappings.get(icon_type.lower(), icon_type)

    def add_icon_mapping(self, icon_type: str, icon_name: str) -> None:
        """
        Add or override an icon mapping.

        Args:
            icon_type: The type identifier.
            icon_name: The icon name to map to.
        """
        self._mappings[icon_type.lower()] = icon_name

    def get_all_mappings(self) -> dict:
        """
        Get all current icon mappings.

        Returns:
            Dictionary of all icon type to icon name mappings.
        """
        return self._mappings.copy()
