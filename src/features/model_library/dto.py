"""
Model Collection DTOs for request models.
"""
from typing import List, Optional
from pydantic import BaseModel


class CreateModelCollectionRequest(BaseModel):
    """Request model for creating a model collection."""
    name: str
    parent_id: Optional[str] = None  # Parent folder id (None = root)


class UpdateModelCollectionRequest(BaseModel):
    """Request model for renaming a model collection."""
    name: str


class MoveModelCollectionRequest(BaseModel):
    """Request model for reparenting a model collection in the folder tree."""
    parent_id: Optional[str] = None  # New parent id (None = move to root)


class BulkMoveModelCollectionsRequest(BaseModel):
    """Request model for reparenting several model collections in one call."""
    collection_ids: List[str]
    parent_id: Optional[str] = None  # New parent id (None = move to root)


class ModelCollectionMembersRequest(BaseModel):
    """Request model for adding/removing model collection members."""
    model_ids: List[str]
