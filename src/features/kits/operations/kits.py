from typing import List, Optional

from src.features.kits import rules
from src.features.kits.records import Kit
from src.features.kits.repository import KitRepository
from src.platform.http.errors import InvalidInputError, NotFoundError

UNSET = object()


def get_kit(repository: KitRepository, owner_id: str, kit_id: str) -> Kit:
    kit = repository.get(kit_id, owner_id)
    if kit is None:
        raise NotFoundError("kit_not_found", "Kit not found")
    return kit


def _free_handle(repository: KitRepository, owner_id: str, base: str, except_kit_id: Optional[str] = None) -> str:
    return rules.free_path_name(base, lambda candidate: repository.handle_taken(owner_id, candidate, except_kit_id))


def create_kit(
    repository: KitRepository, owner_id: str, name: str, handle: Optional[str] = None, label: Optional[str] = None,
) -> Kit:
    display = rules.clean_display_name(name)
    if handle is not None and handle.strip():
        chosen = rules.validate_path_name(handle, "kit")
        if repository.handle_taken(owner_id, chosen):
            raise rules.clash("kit", chosen, lambda candidate: repository.handle_taken(owner_id, candidate))
    else:
        chosen = _free_handle(repository, owner_id, rules.derive_path_name(display, "kit"))
    return repository.create(owner_id, display, chosen, rules.clean_label(label))


def update_kit(
    repository: KitRepository, owner_id: str, kit_id: str,
    name: Optional[str] = None, handle: Optional[str] = None, label=UNSET,
) -> Kit:
    kit = get_kit(repository, owner_id, kit_id)
    previous_handle = kit.handle
    if name is not None:
        kit.name = rules.clean_display_name(name)
    if handle is not None:
        chosen = rules.validate_path_name(handle, "kit")
        if chosen != kit.handle and repository.handle_taken(owner_id, chosen, kit.id):
            raise rules.clash("kit", chosen, lambda candidate: repository.handle_taken(owner_id, candidate, kit.id))
        kit.handle = chosen
    if label is not UNSET:
        kit.label = rules.clean_label(label)
    return repository.update(kit, previous_handle=previous_handle)


def duplicate_kit(repository: KitRepository, owner_id: str, kit_id: str) -> Kit:
    source = get_kit(repository, owner_id, kit_id)
    name = rules.clean_display_name(f"{source.name} copy")
    copy = create_kit(repository, owner_id, name, label=source.label)
    repository.duplicate_items(source.id, copy.id)
    return copy


def delete_kit(repository: KitRepository, owner_id: str, kit_id: str) -> None:
    if not repository.delete(kit_id, owner_id):
        raise NotFoundError("kit_not_found", "Kit not found")


def reorder_kits(repository: KitRepository, owner_id: str, kit_ids: List[str]) -> None:
    owned = repository.get_many(kit_ids, owner_id)
    if len(set(kit_ids)) != len(kit_ids) or set(kit_ids) - set(owned):
        raise InvalidInputError("invalid_order", "The order must list your own kits once each")
    rest, _total = repository.list_kits(owner_id, limit=10_000)
    ordered = list(kit_ids) + [kit.id for kit in rest if kit.id not in owned]
    repository.set_order(owner_id, ordered)
