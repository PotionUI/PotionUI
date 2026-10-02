from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class GenerationStateView:
    owner_id: Optional[str]
    payload: Dict[str, Any]


def resolve_generation_state(live_record: Any, repository: Any, generation_id: str) -> Optional[GenerationStateView]:
    if live_record is not None:
        return GenerationStateView(getattr(live_record, "user_id", None), live_record.model_dump())
    generation = repository.get_by_id(generation_id)
    if generation is None:
        return None
    return GenerationStateView(getattr(generation, "user_id", None), generation.to_dict())
