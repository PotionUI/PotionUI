"""
DTOs for the media editing API.

An edit names a library resource (an `uploads` row) and an ordered list of
operations to apply to it. The result is always another library resource - a
new one, or the same row with different bytes behind it.

The item shape mirrors `LibraryItem` minus its tags rather than importing it:
`src.features.library` is built on top of `src.features.media` (it reads the
`uploads` table through `UploadRepository`), so an import the other way would
close a cycle. Tags are unchanged by an edit anyway - a replace keeps the row
id the `upload_tags` rows point at, and a new resource starts untagged.
"""

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field
from typing_extensions import Annotated


class CropOperation(BaseModel):
    """Cut a rectangle out of the media. Coordinates are pixels from the top-left."""

    type: Literal["crop"]
    x: int
    y: int
    width: int
    height: int


class ResizeOperation(BaseModel):
    """Scale the media. Supplying only one side keeps the aspect ratio."""

    type: Literal["resize"]
    width: Optional[int] = None
    height: Optional[int] = None


class RotateOperation(BaseModel):
    """Rotate clockwise by a quarter turn."""

    type: Literal["rotate"]
    degrees: Literal[90, 180, 270]


class FlipOperation(BaseModel):
    """Mirror the media along one axis."""

    type: Literal["flip"]
    axis: Literal["horizontal", "vertical"]


class TrimOperation(BaseModel):
    """Keep only `[start_seconds, end_seconds)` of a timed medium."""

    type: Literal["trim"]
    start_seconds: float
    end_seconds: float


EditOperation = Annotated[
    Union[CropOperation, ResizeOperation, RotateOperation, FlipOperation, TrimOperation],
    Field(discriminator="type"),
]


class EditMediaRequest(BaseModel):
    """Request model for editing one library resource."""

    operations: List[EditOperation]
    # 'new' leaves the original untouched; 'replace' keeps the row (and so its
    # tags and collection memberships) and swaps the file behind it.
    mode: Literal["new", "replace"] = "new"


class ExtractFrameRequest(BaseModel):
    """Request model for lifting a single video frame out as an image."""

    time_seconds: float = 0.0


class SplitMediaRequest(BaseModel):
    """Request model for splitting one audio resource into fixed-length parts."""

    part_seconds: float


class EditedMediaItem(BaseModel):
    """The library resource an edit produced."""

    id: str
    filename: str
    original_filename: Optional[str] = None
    media_type: str  # 'image' | 'video' | 'audio'
    mime_type: Optional[str] = None
    url: str
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    fps: Optional[float] = None
    size: Optional[int] = None
    created_at: Optional[str] = None


class EditMediaResult(BaseModel):
    """An edit's outcome: the resulting resource, and whether it replaced its source."""

    item: EditedMediaItem
    replaced: bool


class SplitMediaResult(BaseModel):
    """The resources a split produced - each a new resource, in source order."""

    items: List[EditedMediaItem]
