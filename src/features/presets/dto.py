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


class PresetFormSchema(BaseModel):
    preset_id: str
    form_schema: Dict[str, Any]
