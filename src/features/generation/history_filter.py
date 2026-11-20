from dataclasses import dataclass, replace
from typing import List, Literal, Optional


@dataclass(frozen=True)
class HistoryFilter:
    user_id: Optional[str] = None
    status: Optional[str] = None
    error_category: Optional[str] = None
    created_from: Optional[str] = None
    created_to: Optional[str] = None
    completed_from: Optional[str] = None
    completed_to: Optional[str] = None
    tag_ids: Optional[List[str]] = None
    media_type: Optional[str] = None
    search: Optional[str] = None
    mode: Optional[str] = None
    preset_id: Optional[str] = None
    model_name: Optional[str] = None
    min_rating: Optional[int] = None
    favorites_only: bool = False
    collection_id: Optional[str] = None
    include_descendants: bool = True
    unsorted: bool = False
    used_phrasebook_value_id: Optional[str] = None
    system_tag: Optional[str] = None
    grid_id: Optional[str] = None
    semantic_query: Optional[str] = None
    generation_ids: Optional[List[str]] = None
    origin: Optional[str] = None

    @classmethod
    def resolve(cls, base: Optional["HistoryFilter"], overrides: dict) -> "HistoryFilter":
        return replace(base or cls(), **overrides)


def parse_tag_ids(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    return [tid.strip() for tid in raw.split(',') if tid.strip()] or None


def history_filter_query(
    status: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
    completed_from: Optional[str] = None,
    completed_to: Optional[str] = None,
    tag_ids: Optional[str] = None,
    media_type: Optional[str] = None,
    search: Optional[str] = None,
    mode: Optional[str] = None,
    preset_id: Optional[str] = None,
    model_name: Optional[str] = None,
    min_rating: Optional[int] = None,
    favorites_only: bool = False,
    collection_id: Optional[str] = None,
    include_descendants: bool = True,
    unsorted: bool = False,
    used_phrasebook_value_id: Optional[str] = None,
    system_tag: Optional[str] = None,
    semantic_query: Optional[str] = None,
    grid_id: Optional[str] = None,
    origin: Optional[Literal["editor", "mcp", "chat", "tab"]] = None,
) -> HistoryFilter:
    return HistoryFilter(
        status=status,
        created_from=created_from,
        created_to=created_to,
        completed_from=completed_from,
        completed_to=completed_to,
        tag_ids=parse_tag_ids(tag_ids),
        media_type=media_type,
        search=search,
        mode=mode,
        preset_id=preset_id,
        model_name=model_name,
        min_rating=min_rating,
        favorites_only=favorites_only,
        collection_id=collection_id,
        include_descendants=include_descendants,
        unsorted=unsorted,
        used_phrasebook_value_id=used_phrasebook_value_id,
        system_tag=system_tag,
        semantic_query=semantic_query,
        grid_id=grid_id,
        origin=origin,
    )
