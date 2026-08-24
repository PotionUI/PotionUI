"""
Collection DTOs for request models.
"""
from typing import List, Literal, Optional, get_args
from pydantic import BaseModel

# A collection tree is scoped: 'history' folders (generations), 'library'
# folders (uploads), and 'prompts' folders (saved prompts) never mix
# (migrations 137). Every request that names a collection must say which tree
# it means - there is no default. The scope enumeration is open: adding a new
# one is a single addition to this Literal, and ALLOWED_SCOPES (derived from
# it, not hand-kept in sync) is what any non-Pydantic validation checks
# against.
CollectionScope = Literal["history", "library", "prompts"]
ALLOWED_SCOPES: tuple = get_args(CollectionScope)


class CreateCollectionRequest(BaseModel):
    """Request model for creating a collection."""
    name: str
    scope: CollectionScope
    parent_id: Optional[str] = None  # Parent folder id (None = root); must be the same scope


class UpdateCollectionRequest(BaseModel):
    """Request model for renaming a collection."""
    name: str
    scope: CollectionScope


class MoveCollectionRequest(BaseModel):
    """Request model for reparenting a collection in the folder tree."""
    scope: CollectionScope
    parent_id: Optional[str] = None  # New parent id (None = move to root); must be the same scope


class BulkMoveCollectionsRequest(BaseModel):
    """Request model for reparenting several collections in one call."""
    collection_ids: List[str]
    scope: CollectionScope
    parent_id: Optional[str] = None  # New parent id (None = move to root); must be the same scope


class CollectionMembersRequest(BaseModel):
    """Request model for adding/removing generation members."""
    generation_ids: List[str]
    scope: CollectionScope


class CollectionUploadMembersRequest(BaseModel):
    """Request model for adding/removing library upload members."""
    upload_ids: List[str]
    scope: CollectionScope


class CollectionPromptMembersRequest(BaseModel):
    """Request model for adding/removing saved-prompt members."""
    prompt_ids: List[str]
    scope: CollectionScope
