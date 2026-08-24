"""Request DTOs for the media index admin endpoints."""

from typing import Optional

from pydantic import BaseModel, Field


class ProcessPendingRequest(BaseModel):
    pass_type: str = "tags"
    batch_size: int = Field(default=8, ge=1, le=100)


class BackfillRequest(BaseModel):
    retag_stale: bool = False
    pass_type: Optional[str] = None
