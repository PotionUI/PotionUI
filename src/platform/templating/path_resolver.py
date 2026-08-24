"""
Path resolution for template processing.

Provides path mapping for various resource types used in the generation pipeline.
"""

from typing import Optional

from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY


class PathResolver:
    """
    Resolves paths for various resource types.

    Maps logical resource types (checkpoint, lora, etc.) to their filesystem paths.
    """

    # Default path mappings for resource types. `detector`, `wildcard` and `std`
    # are not model depot types - they stay as this resolver's own entries;
    # the rest come from the canonical model type <-> directory mapping.
    DEFAULT_PATH_MAPPINGS = {
        "checkpoint": f"models/{MODEL_TYPE_TO_DIRECTORY['checkpoint']}",
        "lora": f"models/{MODEL_TYPE_TO_DIRECTORY['lora']}",
        "embedding": f"models/{MODEL_TYPE_TO_DIRECTORY['embedding']}",
        "upscaler": f"models/{MODEL_TYPE_TO_DIRECTORY['upscaler']}",
        "detector": "models/detectors",
        "wildcard": "models/wildcards",
        "diffusion_model": f"models/{MODEL_TYPE_TO_DIRECTORY['diffusion_model']}",
        "controlnet": f"models/{MODEL_TYPE_TO_DIRECTORY['controlnet']}",
        "std": "src/std",
    }

    def __init__(self, custom_paths: Optional[dict] = None):
        """
        Initialize PathResolver.

        Args:
            custom_paths: Optional dictionary of custom path mappings to add or override defaults.
        """
        self._paths = self.DEFAULT_PATH_MAPPINGS.copy()
        if custom_paths:
            self._paths.update(custom_paths)

    def get_path_for(self, path_type: str, file_name: Optional[str] = None) -> str:
        """
        Resolve a path based on the type and name.

        Args:
            path_type: The type of the resource (e.g., "lora", "checkpoint").
            file_name: Optional name or identifier of the resource.

        Returns:
            The resolved path as a string.

        Raises:
            ValueError: If the path_type is not supported.
        """
        if path_type not in self._paths:
            raise ValueError(f"Unsupported path type: {path_type}")

        base_path = self._paths[path_type]

        if file_name is None:
            return base_path

        return f"{base_path}/{file_name}"

    def add_path_type(self, path_type: str, base_path: str) -> None:
        """
        Add or override a path type mapping.

        Args:
            path_type: The type identifier.
            base_path: The base path for this type.
        """
        self._paths[path_type] = base_path

    def get_supported_types(self) -> list:
        """
        Get list of supported path types.

        Returns:
            List of supported path type strings.
        """
        return list(self._paths.keys())
