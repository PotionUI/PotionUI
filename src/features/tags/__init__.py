"""
Tag administration.

`operations` (`operations/`) drives tag mutations on behalf of
`TagController` (`routes.py`) and the `organize_gallery` chat tool:
module-level functions taking `TagRepository`/`PluginRegistry` (and, for
delete, the preset repositories its used-by-preset check needs) as explicit
arguments. Reads (`list`/`search`) are pure repository calls + `src.features.
tags.dto.effective_user_id_for_type`, made directly by callers.
"""
from src.features.tags.errors import TagInUseByPresetError

__all__ = ["TagInUseByPresetError"]
