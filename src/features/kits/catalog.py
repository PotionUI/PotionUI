from dataclasses import dataclass
from typing import Dict, List, Optional

from src.features.kits.records import Kit, KitItem
from src.features.kits.repository import KitRepository
from src.features.media.records import Upload


@dataclass
class ItemView:
    kit: Kit
    item: KitItem
    upload: Optional[Upload] = None

    @property
    def missing(self) -> bool:
        return not self.item.is_text and self.relative_path is None

    @property
    def relative_path(self) -> Optional[str]:
        if self.upload is None:
            return None
        name = self.upload.filename
        if not name or name.startswith(".") or "/" in name or "\\" in name or ".." in name:
            return None
        return f"uploads/{name}"


@dataclass
class KitView:
    kit: Kit
    items: List[ItemView]

    def item(self, item_id: str) -> Optional[ItemView]:
        return next((view for view in self.items if view.item.id == item_id), None)

    def item_named(self, name: str) -> Optional[ItemView]:
        return next((view for view in self.items if view.item.name == name), None)


class KitCatalog:
    def __init__(self, repository: KitRepository, owner_id: str):
        self.repository = repository
        self.owner_id = owner_id
        self._by_id: Dict[str, Optional[KitView]] = {}
        self._by_handle: Dict[str, Optional[KitView]] = {}

    def _build(self, kit: Kit) -> KitView:
        items = self.repository.items(kit.id)
        uploads = self.repository.uploads_by_id(item.media_ref for item in items)
        return KitView(kit, [ItemView(kit, item, self._owned(uploads.get(item.media_ref)) if item.media_ref else None) for item in items])

    def _owned(self, upload: Optional[Upload]) -> Optional[Upload]:
        return upload if upload is not None and upload.user_id == self.owner_id else None

    def by_id(self, kit_id: str) -> Optional[KitView]:
        if kit_id not in self._by_id:
            kit = self.repository.get(kit_id, self.owner_id)
            self._by_id[kit_id] = self._build(kit) if kit else None
        return self._by_id[kit_id]

    def by_handle(self, handle: str) -> Optional[KitView]:
        if handle not in self._by_handle:
            kit = self.repository.get_by_handle(self.owner_id, handle)
            view = self.by_id(kit.id) if kit else None
            self._by_handle[handle] = view
        return self._by_handle[handle]
