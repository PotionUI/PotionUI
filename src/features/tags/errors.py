"""
Tag-specific exceptions.
"""
from typing import List


class TagInUseByPresetError(ValueError):
    """Raised by delete_tag when the tag is referenced by an installed preset's
    stored `configuration:` values (e.g. a `model_tags` entry). No force flag -
    the admin must unset it from the preset's configuration first. See
    docs/presets.md "Configuration (admin-set)"."""

    def __init__(self, tag_id: str, used_by: List[dict]):
        self.tag_id = tag_id
        self.used_by = used_by
        super().__init__(f"Tag '{tag_id}' is used by {len(used_by)} preset configuration(s)")
