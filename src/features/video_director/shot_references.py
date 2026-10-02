from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.features.presets.schema import PROMPT_RESOURCE_INDEX_PLACEHOLDER
from src.features.prompt.resources import RESOURCE_MARKER_RE, media_item_keys, spec_for_item, specs_by_field
from src.platform.filesystem.media_kinds import MEDIA_KINDS, media_item_kind


@dataclass(frozen=True)
class ShotReferences:
    texts: List[str]
    indices: List[int]
    kinds: List[str]
    problems: List[str]


def packed_reference_pool(reference_fields: Sequence[str], form_data: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    pool: List[Tuple[str, Any]] = []
    for field in reference_fields:
        raw = form_data.get(field)
        items = raw if isinstance(raw, list) else ([] if raw is None or raw == "" else [raw])
        pool.extend((field, item) for item in items if media_item_keys(item))
    return sorted(pool, key=lambda entry: _kind_rank(media_item_kind(entry[1])))


def _kind_rank(kind: Optional[str]) -> int:
    return MEDIA_KINDS.index(kind) if kind in MEDIA_KINDS else len(MEDIA_KINDS)


def _pool_index(pool: Sequence[Tuple[str, Any]], field: str, item_key: str) -> Optional[int]:
    for index, (pool_field, item) in enumerate(pool):
        if pool_field == field and item_key in media_item_keys(item):
            return index
    return None


def derive_shot_references(
    texts: Sequence[str],
    pool: Sequence[Tuple[str, Any]],
    prompt_resources: Sequence[Mapping[str, Any]],
) -> ShotReferences:
    specs = specs_by_field(prompt_resources)
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
            index = _pool_index(pool, field, item_key)
            if index is None:
                note(f"the prompt references an item that was removed from this field ({item_key})")
                continue
            if spec_for_item(specs[field], pool[index][1]) is None:
                note(
                    f"the prompt references a {media_item_kind(pool[index][1]) or 'unrecognised'} item "
                    f"({item_key}), which this field's prompt cannot reference"
                )
                continue
            cited.add(index)

    indices = sorted(cited)
    kinds: List[str] = []
    positions: Dict[int, int] = {}
    per_kind: Dict[str, int] = {}
    for index in indices:
        spec = spec_for_item(specs[pool[index][0]], pool[index][1])
        kind = str(spec.get("kind") or pool[index][0])
        per_kind[kind] = per_kind.get(kind, 0) + 1
        positions[index] = per_kind[kind]
        kinds.append(kind)

    def replace(match: Any) -> str:
        field, item_key = match.group(1), match.group(2)
        index = _pool_index(pool, field, item_key) if field in specs else None
        if index is None or index not in positions:
            return match.group(0)
        spec = spec_for_item(specs[field], pool[index][1])
        return str(spec["token"]).replace(PROMPT_RESOURCE_INDEX_PLACEHOLDER, str(positions[index]))

    resolved = [
        RESOURCE_MARKER_RE.sub(replace, text) if isinstance(text, str) and "@[" in text else text
        for text in texts
    ]
    return ShotReferences(texts=resolved, indices=indices, kinds=kinds, problems=problems)
