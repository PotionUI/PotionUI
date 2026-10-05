from typing import Any, Iterable, Optional, Protocol, Set


class OrganizeVisibility(Protocol):
    def is_restricted(self, user_id: str) -> bool: ...

    def viewable_generation_ids(self, user_id: str, generation_ids: Iterable[str]) -> Set[str]: ...


class OpenVisibility:
    def is_restricted(self, user_id: str) -> bool:
        return False

    def viewable_generation_ids(self, user_id: str, generation_ids: Iterable[str]) -> Set[str]:
        return set(generation_ids)


class ContentSafetyVisibility:
    def __init__(self, content_safety: Optional[Any]):
        self.content_safety = content_safety

    def is_restricted(self, user_id: str) -> bool:
        if self.content_safety is None:
            return False
        return bool(self.content_safety.is_restricted(user_id))

    def viewable_generation_ids(self, user_id: str, generation_ids: Iterable[str]) -> Set[str]:
        ids = list(generation_ids)
        if self.content_safety is None:
            return set(ids)
        return set(self.content_safety.viewable_generation_ids(user_id, ids))
