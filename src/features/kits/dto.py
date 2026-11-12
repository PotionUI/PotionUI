from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.features.kits.rules import MAX_ITEMS_PER_KIT


class CreateKitRequest(BaseModel):
    name: str
    handle: Optional[str] = None
    label: Optional[str] = None


class UpdateKitRequest(BaseModel):
    name: Optional[str] = None
    handle: Optional[str] = None
    label: Optional[str] = None


class AddItemRequest(BaseModel):
    upload_id: Optional[str] = None
    text: Optional[str] = None
    name: Optional[str] = None


class UpdateItemRequest(BaseModel):
    name: Optional[str] = None
    text: Optional[str] = None


class OrderRequest(BaseModel):
    ids: List[str]


class NewKitSpec(BaseModel):
    name: str
    label: Optional[str] = None


class AddLibraryItemsRequest(BaseModel):
    upload_ids: List[str] = Field(min_length=1, max_length=MAX_ITEMS_PER_KIT)
    kit_id: Optional[str] = None
    new_kit: Optional[NewKitSpec] = None
    names: Dict[str, str] = Field(default_factory=dict)


class ResolveRequest(BaseModel):
    text: str = ""
    cites: List[str] = Field(default_factory=list)
    loaded: Optional[List[str]] = None
    field: Optional[str] = None
    preset_id: Optional[str] = None
    mode: Optional[str] = None
    form_data: Dict[str, Any] = Field(default_factory=dict)
