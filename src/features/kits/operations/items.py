from typing import Callable, Dict, List, Optional, Sequence, Tuple

from src.features.kits import rules
from src.features.kits.operations.kits import create_kit, get_kit
from src.features.kits.records import Kit, KitItem
from src.features.kits.repository import KitRepository
from src.platform.http.errors import ConflictError, InvalidInputError, NotFoundError


def get_item(repository: KitRepository, kit: Kit, item_id: str) -> KitItem:
    item = repository.get_item(kit.id, item_id)
    if item is None:
        raise NotFoundError("kit_item_not_found", "Kit item not found")
    return item


def _room_for(repository: KitRepository, kit: Kit, adding: int = 1) -> None:
    if repository.count_items(kit.id) + adding > rules.MAX_ITEMS_PER_KIT:
        raise ConflictError(
            "kit_full", f"A kit holds at most {rules.MAX_ITEMS_PER_KIT} items", limit=rules.MAX_ITEMS_PER_KIT,
        )


def _item_name(repository: KitRepository, kit: Kit, kind: str, requested: Optional[str], taken: Sequence[str]) -> str:
    names = {item.name for item in repository.items(kit.id)} | set(taken)
    if requested is None or not requested.strip():
        return rules.default_item_name(kind, names)
    chosen = rules.validate_path_name(requested, "item")
    if chosen in names:
        raise rules.clash("item", chosen, lambda candidate: candidate in names)
    return chosen


def add_media_item(
    repository: KitRepository, owner_id: str, kit_id: str, upload_id: str, name: Optional[str] = None,
) -> KitItem:
    kit = get_kit(repository, owner_id, kit_id)
    upload = repository.owned_uploads(owner_id, [upload_id]).get(upload_id)
    if upload is None:
        raise NotFoundError("library_item_not_found", "Library item not found")
    _room_for(repository, kit)
    return repository.add_item(kit.id, upload.media_type, _item_name(repository, kit, upload.media_type, name, ()), media_ref=upload.id)


def add_text_item(repository: KitRepository, owner_id: str, kit_id: str, text: str, name: Optional[str] = None) -> KitItem:
    kit = get_kit(repository, owner_id, kit_id)
    body = clean_text(text)
    _room_for(repository, kit)
    return repository.add_item(kit.id, rules.KIND_TEXT, _item_name(repository, kit, rules.KIND_TEXT, name, ()), text=body)


def clean_text(text: Optional[str]) -> str:
    body = (text or "").strip()
    if not body:
        raise InvalidInputError("kit_text_required", "A text item needs some text")
    if len(body) > rules.MAX_TEXT_CHARS:
        raise InvalidInputError("kit_text_too_long", f"A text item holds at most {rules.MAX_TEXT_CHARS} characters")
    return body


def add_library_items(
    repository: KitRepository,
    owner_id: str,
    upload_ids: Sequence[str],
    kit_id: Optional[str] = None,
    new_kit_name: Optional[str] = None,
    new_kit_label: Optional[str] = None,
    names: Optional[Dict[str, str]] = None,
) -> Tuple[Kit, List[KitItem]]:
    ordered = list(dict.fromkeys(upload_ids))
    if not ordered:
        raise InvalidInputError("no_items", "Pick at least one Library item")
    owned = repository.owned_uploads(owner_id, ordered)
    missing = [upload_id for upload_id in ordered if upload_id not in owned]
    if missing:
        raise NotFoundError("library_item_not_found", "Library item not found", missing=missing)
    kinds = {upload_id: owned[upload_id].media_type for upload_id in ordered}
    return _add_batch(repository, owner_id, ordered, kinds, lambda ids: {i: i for i in ids}, kit_id, new_kit_name, new_kit_label, names)


def add_history_items(
    repository: KitRepository,
    copy_to_library: Callable[[str, str], str],
    owner_id: str,
    file_ids: Sequence[str],
    kit_id: Optional[str] = None,
    new_kit_name: Optional[str] = None,
    new_kit_label: Optional[str] = None,
    names: Optional[Dict[str, str]] = None,
) -> Tuple[Kit, List[KitItem]]:
    ordered = list(dict.fromkeys(file_ids))
    if not ordered:
        raise InvalidInputError("no_items", "Pick at least one History item")
    owned = repository.owned_generation_files(owner_id, ordered)
    missing = [file_id for file_id in ordered if file_id not in owned]
    if missing:
        raise NotFoundError("history_item_not_found", "History item not found", missing=missing)

    def copy_all(ids: Sequence[str]) -> Dict[str, str]:
        try:
            return {file_id: copy_to_library(file_id, owner_id) for file_id in ids}
        except ValueError:
            raise NotFoundError("history_item_not_found", "History item not found", missing=list(ids))

    return _add_batch(repository, owner_id, ordered, owned, copy_all, kit_id, new_kit_name, new_kit_label, names)


def _add_batch(
    repository: KitRepository,
    owner_id: str,
    ordered: List[str],
    kinds: Dict[str, str],
    resolve_refs: Callable[[Sequence[str]], Dict[str, str]],
    kit_id: Optional[str],
    new_kit_name: Optional[str],
    new_kit_label: Optional[str],
    names: Optional[Dict[str, str]],
) -> Tuple[Kit, List[KitItem]]:
    if (kit_id is None) == (new_kit_name is None):
        raise InvalidInputError("kit_target_required", "Name an existing kit or a new one, not both")
    names = names or {}
    if kit_id is not None:
        kit = get_kit(repository, owner_id, kit_id)
        _room_for(repository, kit, len(ordered))
    plan: List[Tuple[str, str]] = []
    used: List[str] = []
    existing = set() if kit_id is None else {item.name for item in repository.items(kit_id)}
    for source_id in ordered:
        kind = kinds[source_id]
        requested = names.get(source_id)
        if requested is not None and requested.strip():
            chosen = rules.validate_path_name(requested, "item")
            if chosen in existing | set(used):
                taken = existing | set(used)
                raise rules.clash("item", chosen, lambda candidate: candidate in taken)
        else:
            chosen = rules.default_item_name(kind, existing | set(used))
        used.append(chosen)
        plan.append((source_id, chosen))
    if len(ordered) > rules.MAX_ITEMS_PER_KIT:
        raise ConflictError("kit_full", f"A kit holds at most {rules.MAX_ITEMS_PER_KIT} items", limit=rules.MAX_ITEMS_PER_KIT)
    refs = resolve_refs(ordered)
    if kit_id is None:
        kit = create_kit(repository, owner_id, new_kit_name, label=new_kit_label)
    added = [
        repository.add_item(kit.id, kinds[source_id], chosen, media_ref=refs[source_id])
        for source_id, chosen in plan
    ]
    return repository.get(kit.id, owner_id), added


def update_item(
    repository: KitRepository, owner_id: str, kit_id: str, item_id: str,
    name: Optional[str] = None, text: Optional[str] = None,
) -> KitItem:
    kit = get_kit(repository, owner_id, kit_id)
    item = get_item(repository, kit, item_id)
    if name is not None:
        chosen = rules.validate_path_name(name, "item")
        if chosen != item.name:
            names = {other.name for other in repository.items(kit.id) if other.id != item.id}
            if chosen in names:
                raise rules.clash("item", chosen, lambda candidate: candidate in names)
            item.name = chosen
    if text is not None:
        if not item.is_text:
            raise InvalidInputError("not_a_text_item", "Only a text item has text")
        item.text = clean_text(text)
    return repository.update_item(item)


def remove_item(repository: KitRepository, owner_id: str, kit_id: str, item_id: str) -> None:
    kit = get_kit(repository, owner_id, kit_id)
    get_item(repository, kit, item_id)
    repository.delete_item(kit.id, item_id)


def reorder_items(repository: KitRepository, owner_id: str, kit_id: str, item_ids: List[str]) -> None:
    kit = get_kit(repository, owner_id, kit_id)
    current = [item.id for item in repository.items(kit.id)]
    if sorted(item_ids) != sorted(current):
        raise InvalidInputError("invalid_order", "The order must list every item of the kit once")
    repository.set_item_order(kit.id, item_ids)
