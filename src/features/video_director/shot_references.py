from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.features.presets.schema import PROMPT_RESOURCE_INDEX_PLACEHOLDER
from src.features.prompt.reference_pool import (
    RefModMembersResolver,
    RefModPoolMember,
    build_reference_pool,
    pool_entry_keys,
    refmod_fields,
)
from src.features.prompt.resources import RESOURCE_MARKER_RE, spec_for_item, specs_by_field
from src.platform.filesystem.media_kinds import media_item_kind


@dataclass(frozen=True)
class ShotReferences:
    texts: List[str]
    indices: List[int]
    kinds: List[str]
    problems: List[str]


def packed_reference_pool(
    reference_fields: Sequence[str],
    form_data: Mapping[str, Any],
    prompt_resources: Sequence[Mapping[str, Any]] = (),
    refmod_members: Optional[RefModMembersResolver] = None,
) -> List[Tuple[str, Any]]:
    return build_reference_pool(reference_fields, form_data, prompt_resources, refmod_members)[0]


def _pool_indices(pool: Sequence[Tuple[str, Any]], field: str, item_key: str) -> List[int]:
    return [
        index for index, entry in enumerate(pool)
        if entry[0] == field and item_key in pool_entry_keys(entry)
    ]


def _entry_spec(specs: Dict[str, List[Mapping[str, Any]]], entry: Tuple[str, Any]) -> Optional[Mapping[str, Any]]:
    field, item = entry
    if isinstance(item, RefModPoolMember):
        return next((spec for spec in specs.get(field, []) if spec.get("kind") == item.kind), None)
    return spec_for_item(specs[field], item)


def _entry_kind_name(entry: Tuple[str, Any]) -> str:
    item = entry[1]
    return item.kind if isinstance(item, RefModPoolMember) else (media_item_kind(item) or "unrecognised")


def derive_shot_references(
    texts: Sequence[str],
    pool: Sequence[Tuple[str, Any]],
    prompt_resources: Sequence[Mapping[str, Any]],
) -> ShotReferences:
    specs = specs_by_field(prompt_resources)
    bundle_fields = refmod_fields(prompt_resources)
    problems: List[str] = []
    cited: set = set()

    def note(message: str) -> None:
        if message not in problems:
            problems.append(message)

    for text in texts:
        if not isinstance(text, str) or "@[" not in text:
            continue
        for match in RESOURCE_MARKER_RE.finditer(text):
            field, item_key = match.group(1), match.group(2)
            if field not in specs:
                note(f"the prompt references '{field}', which this mode's prompt cannot reference")
                continue
            matches = _pool_indices(pool, field, item_key)
            if not matches:
                note(f"the prompt references an item that was removed from this field ({item_key})")
                continue
            unsupported = [index for index in matches if _entry_spec(specs, pool[index]) is None]
            if unsupported:
                kind = _entry_kind_name(pool[unsupported[0]])
                article = "an" if kind[:1] in "aeiou" else "a"
                what = f"RefMod with {article} {kind} member" if field in bundle_fields else f"{kind} item"
                note(f"the prompt references a {what} ({item_key}), which this field's prompt cannot reference")
                continue
            cited.update(matches)

    indices = sorted(cited)
    kinds: List[str] = []
    positions: Dict[int, int] = {}
    per_kind: Dict[str, int] = {}
    for index in indices:
        spec = _entry_spec(specs, pool[index])
        kind = str(spec.get("kind") or pool[index][0])
        per_kind[kind] = per_kind.get(kind, 0) + 1
        positions[index] = per_kind[kind]
        kinds.append(kind)

    def replace(match: Any) -> str:
        field, item_key = match.group(1), match.group(2)
        matches = [index for index in _pool_indices(pool, field, item_key) if index in positions] if field in specs else []
        if not matches:
            return match.group(0)
        return " ".join(
            str(_entry_spec(specs, pool[index])["token"]).replace(PROMPT_RESOURCE_INDEX_PLACEHOLDER, str(positions[index]))
            for index in matches
        )

    resolved = [
        RESOURCE_MARKER_RE.sub(replace, text) if isinstance(text, str) and "@[" in text else text
        for text in texts
    ]
    return ShotReferences(texts=resolved, indices=indices, kinds=kinds, problems=problems)
