from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.features.prompt.resource_sources import BundleMembersResolver, entry_source, source_label
from src.features.prompt.resources import media_item_keys
from src.platform.filesystem.media_kinds import MEDIA_KINDS, media_item_kind

MAX_BUNDLE_COPIES = 12


@dataclass(frozen=True)
class BundlePoolMember:
    source: str
    key: str
    kind: str
    row: Mapping[str, Any]


def bundle_field_sources(resources: Iterable[Any]) -> Dict[str, str]:
    sources: Dict[str, str] = {}
    for entry in resources or []:
        source = entry_source(entry)
        if source and entry.get("field"):
            sources.setdefault(str(entry["field"]), source)
    return sources


def bundle_item_key(row: Any) -> Optional[str]:
    model = row.get("model") if isinstance(row, Mapping) else None
    return model.strip() if isinstance(model, str) and model.strip() else None


def bundle_row_active(strength: Any) -> bool:
    try:
        return float(1.0 if strength is None or strength == "" else strength) > 0
    except (TypeError, ValueError):
        return True


def bundle_row_copies(value: Any, key: str, label: str) -> int:
    if value is None or value == "":
        return 1
    try:
        copies = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} {key!r}: copies must be a whole number, got {value!r}") from error
    if not copies.is_integer() or not 1 <= copies <= MAX_BUNDLE_COPIES:
        raise ValueError(
            f"{label} {key!r}: copies must be a whole number from 1 to {MAX_BUNDLE_COPIES}, got {value!r}"
        )
    return int(copies)


def pool_entry_kind(entry: Tuple[str, Any]) -> Optional[str]:
    item = entry[1]
    return item.kind if isinstance(item, BundlePoolMember) else media_item_kind(item)


def pool_entry_keys(entry: Tuple[str, Any]) -> Tuple[str, ...]:
    item = entry[1]
    return (item.key,) if isinstance(item, BundlePoolMember) else media_item_keys(item)


def pool_entry_name(entry: Tuple[str, Any]) -> Optional[str]:
    item = entry[1]
    if not isinstance(item, Mapping):
        return None
    kit_item = item.get("kit_item")
    if isinstance(kit_item, Mapping) and isinstance(kit_item.get("name"), str):
        return kit_item["name"]
    return None


def _kind_rank(kind: Optional[str]) -> int:
    return MEDIA_KINDS.index(kind) if kind in MEDIA_KINDS else len(MEDIA_KINDS)


def _field_items(raw: Any) -> List[Any]:
    if isinstance(raw, list):
        return raw
    return [] if raw is None or raw == "" else [raw]


def _bundle_entries(
    field: str, source: str, rows: List[Any], resolver: Optional[BundleMembersResolver], problems: List[str],
) -> List[Tuple[str, Any]]:
    label = source_label(source)
    entries: List[Tuple[str, Any]] = []
    for row in rows:
        key = bundle_item_key(row)
        if key is None or not bundle_row_active(row.get("strength")):
            continue
        if resolver is None:
            problems.append(f"{field}: the {label} {key} cannot be read here")
            continue
        try:
            kinds = list(resolver(source, key))
            copies = bundle_row_copies(row.get("copies"), key, label)
        except ValueError as error:
            problems.append(f"{field}: {error}")
            continue
        entries.extend((field, BundlePoolMember(source, key, kind, row)) for _ in range(copies) for kind in kinds)
    return entries


def build_reference_pool(
    reference_fields: Sequence[str],
    form_data: Mapping[str, Any],
    resources: Iterable[Any] = (),
    bundle_members: Optional[BundleMembersResolver] = None,
) -> Tuple[List[Tuple[str, Any]], List[str]]:
    sources = bundle_field_sources(resources)
    natives: List[Tuple[str, Any]] = []
    bundles: List[Tuple[str, Any]] = []
    problems: List[str] = []
    for field in reference_fields:
        items = _field_items(form_data.get(field))
        if field in sources:
            bundles.extend(_bundle_entries(field, sources[field], items, bundle_members, problems))
        else:
            natives.extend((field, item) for item in items if media_item_keys(item))
    pool = sorted(natives + bundles, key=lambda entry: _kind_rank(pool_entry_kind(entry)))
    return pool, problems


def bundle_member_positions(pool: Sequence[Tuple[str, Any]]) -> Dict[int, int]:
    counts: Dict[str, int] = {}
    positions: Dict[int, int] = {}
    for index, entry in enumerate(pool):
        kind = pool_entry_kind(entry)
        if kind is None:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if isinstance(entry[1], BundlePoolMember):
            positions[index] = counts[kind]
    return positions
