"""
Tag domain module.

Exports TagManager for use by controllers and other modules.
"""
from src.features.tags.manager import TagManager, TagInUseByPresetError

__all__ = ["TagManager", "TagInUseByPresetError"]
