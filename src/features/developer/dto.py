from typing import List, Optional
from pydantic import BaseModel

from src.features.presets.style_renderer import (
    STYLE_PREVIEW_LONG_EDGE_DEFAULT,
    STYLE_PREVIEW_SEED_DEFAULT,
)


class RenderPresetStylesRequest(BaseModel):
    style_ids: Optional[List[str]] = None
    long_edge: int = STYLE_PREVIEW_LONG_EDGE_DEFAULT
    seed: int = STYLE_PREVIEW_SEED_DEFAULT
