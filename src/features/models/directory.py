"""The models directory: where each type of model file lives on disk.

The model *objects* that pipes pass around are `src.pipelines.models`; this is
the filesystem side - the directory layout only.
"""

from src.platform.filesystem.model_types import MODEL_TYPE_TO_DIRECTORY
from pathlib import Path


class ModelDirectories:
    """
    Owns the models directory layout: where each model type lives on disk.

    Downloading is NOT its job. Models are downloaded on demand through the
    core download queue (src/features/downloads), which authenticates via the
    provider registry (see docs/backends.md for the same registry pattern
    applied to engines).
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def _get_model_subdir(self, model_type: str) -> str:
        """Get the appropriate subdirectory for a model type"""
        return MODEL_TYPE_TO_DIRECTORY.get(model_type.lower(), 'checkpoint')

    def get_model_dir(self, model_type: str) -> Path:
        """Get the directory for a specific model type"""
        subdir = self._get_model_subdir(model_type)
        return self.base_path / subdir
