from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ModelLayoutSummary(BaseModel):
    id: str
    label: str
    source: str
    plugin_id: Optional[str] = None


class ModelLayoutListResponse(BaseModel):
    layouts: List[ModelLayoutSummary] = Field(default_factory=list)
    load_errors: Dict[str, List[str]] = Field(default_factory=dict)
