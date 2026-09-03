from typing import Dict, List, Any, Optional
from pydantic import BaseModel

class PresetInfo(BaseModel):
    id: str
    name: str
    version: str
    description: Optional[str] = None
    tags: List[str] = []
    category: Optional[str] = None
    source: Optional[str] = None
    engine: Optional[str] = None
    media: Optional[dict] = None
    # Optional hardware guidance, e.g. {"min_vram_gb": 12, "recommended_vram_gb": 16}.
    # See docs/presets.md "Hardware requirements".
    requires: Optional[dict] = None
    # Last-evaluated requirements summary, e.g.
    # {"ok": 3, "missing": 1, "unknown": 0, "optional_missing": 1} - a missing
    # entry marked `optional: true` counts under `optional_missing`, not
    # `missing`. `None` until GET /api/presets/{id}/requirements has run at
    # least once for this preset+backend - never computed by the list/detail
    # endpoints themselves. See docs/presets.md "Requirements".
    requirements_summary: Optional[dict] = None


class PresetFormSchema(BaseModel):
    preset_id: str
    form_schema: Dict[str, Any]
