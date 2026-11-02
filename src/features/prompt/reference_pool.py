from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from src.features.models.form_refs import is_model_ref, model_id_of
from src.features.models.locator import ModelFileUnavailable
from src.features.prompt.resources import media_item_keys
from src.pipelines.pipes._shared.generation.refmod_header import read_refmod_members, refmod_copies, refmod_entry_active
from src.platform.filesystem.media_kinds import MEDIA_KINDS, media_item_kind

REFMOD_SOURCE = "refmod"

RefModMembersResolver = Callable[[str], Sequence[str]]


@dataclass(frozen=True)
class RefModPoolMember:
    key: str
    kind: str
    row: Mapping[str, Any]


def refmod_fields(resources: Iterable[Any]) -> Set[str]:
    return {
        str(entry["field"]) for entry in resources or []
        if isinstance(entry, Mapping) and entry.get("field") and entry.get("source") == REFMOD_SOURCE
    }


def refmod_item_key(row: Any) -> Optional[str]:
    model = row.get("model") if isinstance(row, Mapping) else None
    return model.strip() if isinstance(model, str) and model.strip() else None


def pool_entry_kind(entry: Tuple[str, Any]) -> Optional[str]:
    item = entry[1]
    return item.kind if isinstance(item, RefModPoolMember) else media_item_kind(item)


def pool_entry_keys(entry: Tuple[str, Any]) -> Tuple[str, ...]:
    item = entry[1]
    return (item.key,) if isinstance(item, RefModPoolMember) else media_item_keys(item)


def _kind_rank(kind: Optional[str]) -> int:
    return MEDIA_KINDS.index(kind) if kind in MEDIA_KINDS else len(MEDIA_KINDS)


def _field_items(raw: Any) -> List[Any]:
    if isinstance(raw, list):
        return raw
    return [] if raw is None or raw == "" else [raw]


def _refmod_entries(
    field: str, rows: List[Any], resolver: Optional[RefModMembersResolver], problems: List[str],
) -> List[Tuple[str, Any]]:
    entries: List[Tuple[str, Any]] = []
    for row in rows:
        key = refmod_item_key(row)
        if key is None or not refmod_entry_active(row.get("strength")):
            continue
        if resolver is None:
            problems.append(f"{field}: the RefMod {key} cannot be read here")
            continue
        try:
            kinds = list(resolver(key))
            copies = refmod_copies(row.get("copies"), key)
        except ValueError as error:
            problems.append(f"{field}: {error}")
            continue
        entries.extend((field, RefModPoolMember(key, kind, row)) for _ in range(copies) for kind in kinds)
    return entries


def build_reference_pool(
    reference_fields: Sequence[str],
    form_data: Mapping[str, Any],
    resources: Iterable[Any] = (),
    refmod_members: Optional[RefModMembersResolver] = None,
) -> Tuple[List[Tuple[str, Any]], List[str]]:
    mod_fields = refmod_fields(resources)
    natives: List[Tuple[str, Any]] = []
    mods: List[Tuple[str, Any]] = []
    problems: List[str] = []
    for field in reference_fields:
        items = _field_items(form_data.get(field))
        if field in mod_fields:
            mods.extend(_refmod_entries(field, items, refmod_members, problems))
        else:
            natives.extend((field, item) for item in items if media_item_keys(item))
    pool = sorted(natives + mods, key=lambda entry: _kind_rank(pool_entry_kind(entry)))
    return pool, problems


def refmod_member_positions(pool: Sequence[Tuple[str, Any]]) -> Dict[int, int]:
    counts: Dict[str, int] = {}
    positions: Dict[int, int] = {}
    for index, entry in enumerate(pool):
        kind = pool_entry_kind(entry)
        if kind is None:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if isinstance(entry[1], RefModPoolMember):
            positions[index] = counts[kind]
    return positions


def refmod_members_resolver(locator: Any) -> RefModMembersResolver:
    def resolve(key: str) -> List[str]:
        return [member.kind for member in read_refmod_members(refmod_path(locator, key))]

    return resolve


def refmod_path(locator: Any, key: str) -> str:
    if not is_model_ref(key):
        return key
    try:
        return str(locator.path_for_model(model_id_of(key)))
    except ModelFileUnavailable as error:
        raise ValueError(f"the RefMod {key} is not available: {error}") from error
