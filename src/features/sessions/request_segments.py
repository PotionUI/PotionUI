import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from src.features.sessions.request_variables import Random



def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _encode_path(path: str) -> str:
    return f"#[{path}]" if " " in path else f"#{path}"


def rich_text_to_plain_text(value: str, chips: Mapping[str, Any]) -> str:
    if not value or not chips:
        return value
    replacements: List[Tuple[int, int, str]] = []
    for chip_id, chip in chips.items():
        path = _text(chip.get("categoryPath"))
        bracketed = f"#[{path}]"
        plain = f"#{path}"
        spaced = " " in path
        pattern = bracketed if spaced else plain
        position = value.find(pattern)
        if position == -1:
            pattern = plain if spaced else bracketed
            position = value.find(pattern)
        occurrence = 0
        while position != -1:
            same_paths = 0
            for other_id, other in chips.items():
                if _text(other.get("categoryPath")) == path and other_id != chip_id:
                    other_position = value.find(_encode_path(_text(other.get("categoryPath"))))
                    if other_position != -1 and other_position < position:
                        same_paths += 1
            if same_paths == occurrence:
                replacement = chip.get("value")
                replacements.append((position, len(pattern), "" if replacement is None else str(replacement)))
                break
            occurrence += 1
            position = value.find(pattern, position + len(pattern))
    result = value
    for position, length, replacement in sorted(replacements, key=lambda item: -item[0]):
        result = result[:position] + replacement + result[position + length:]
    return result


def is_segment_enabled(segment: Mapping[str, Any]) -> bool:
    enabled = segment.get("enabled")
    if isinstance(enabled, bool):
        return enabled
    return not segment.get("isDisabled")


def apply_segment_affixes(segment: Mapping[str, Any], resolved: str) -> str:
    prefix, suffix = segment.get("prefix"), segment.get("suffix")
    if not prefix and not suffix:
        return resolved
    trimmed = resolved.strip()
    return f"{prefix or ''}{trimmed}{suffix or ''}" if trimmed else ""


def flatten_rich_segments(segments: Optional[Sequence[Mapping[str, Any]]], join: str = "space") -> str:
    result = ""
    for segment in segments or []:
        if not is_segment_enabled(segment):
            continue
        chips = segment.get("chips") or {}
        content = _text(segment.get("content"))
        resolved = rich_text_to_plain_text(content, chips) if chips else content
        trimmed = resolved.strip()
        if not trimmed:
            continue
        if result:
            result += "\n\n" if join == "paragraph" else " "
        result += apply_segment_affixes(segment, trimmed)
    return result.strip()


def _blank(value: Any) -> bool:
    return not _text(value).strip()


def is_pristine_blank_segment(segment: Mapping[str, Any]) -> bool:
    kind = segment.get("type")
    return (
        (kind if kind is not None else "content") == "content"
        and _blank(segment.get("content"))
        and not (segment.get("chips") or {})
        and is_segment_enabled(segment)
        and _blank(segment.get("name") or segment.get("title"))
        and _blank(segment.get("color"))
        and _blank(segment.get("description"))
        and _blank(segment.get("prefix"))
        and _blank(segment.get("suffix"))
    )


def is_pristine_placeholder_list(segments: Sequence[Mapping[str, Any]]) -> bool:
    return len(segments) == 1 and is_pristine_blank_segment(segments[0])


def _seed_side(text: Any, segments: Optional[List[Any]], segment_id: str) -> Optional[List[Dict[str, Any]]]:
    listed = segments or []
    if _blank(text):
        return None
    if len(listed) > 0 and not is_pristine_placeholder_list(listed):
        return None
    return [{"id": segment_id, "type": "content", "content": text, "chips": {}, "enabled": True, "isDisabled": False}]


def seed_segments_from_text(fields: Mapping[str, Any]) -> Dict[str, Any]:
    positive = _seed_side(fields.get("prompt"), fields.get("promptSegments"), "seeded-positive")
    negative = _seed_side(fields.get("negativePrompt"), fields.get("negativePromptSegments"), "seeded-negative")
    seeded = dict(fields)
    if positive:
        seeded["promptSegments"] = positive
    if negative:
        seeded["negativePromptSegments"] = negative
    return seeded


def seed_prompt_tabs(tabs: Optional[List[Any]]) -> Optional[List[Any]]:
    if not tabs:
        return tabs
    return [seed_segments_from_text(entry) if isinstance(entry, Mapping) else entry for entry in tabs]


def prompt_count(tab: Mapping[str, Any]) -> int:
    tabs = tab.get("promptTabs")
    return len(tabs) if tabs and len(tabs) > 1 else 1


def active_prompt_index(tab: Mapping[str, Any]) -> int:
    count = prompt_count(tab)
    raw = tab.get("activePromptTab")
    try:
        index = math.floor(float(0 if raw is None else raw))
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(max(index, 0), count - 1)


def prompt_list(tab: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if prompt_count(tab) < 2:
        return [
            {
                "promptSegments": tab.get("promptSegments") or [],
                "negativePromptSegments": tab.get("negativePromptSegments") or [],
                "prompt": tab.get("prompt") or "",
                "negativePrompt": tab.get("negativePrompt") or "",
            }
        ]
    return list(tab["promptTabs"])


def has_prompt_text(entry: Mapping[str, Any]) -> bool:
    return bool(flatten_rich_segments(entry.get("promptSegments") or []).strip())


def first_empty_prompt(entries: Sequence[Mapping[str, Any]]) -> int:
    for index, entry in enumerate(entries):
        if not has_prompt_text(entry):
            return index
    return -1


def _shuffle_chip(chip: Dict[str, Any], random: Random) -> Dict[str, Any]:
    values = chip.get("allValues") or []
    if not chip.get("shuffle") or len(values) <= 1:
        return chip
    available = [value for value in values if value.get("id") != chip.get("valueId")]
    if not available:
        return chip
    picked = available[math.floor(random() * len(available))]
    return {**chip, "valueId": picked.get("id"), "label": picked.get("label"), "value": picked.get("value")}


def shuffle_segment_chips(segments: Optional[List[Any]], random: Random) -> Tuple[List[Any], bool]:
    shuffled = list(segments or [])
    changed = False
    for index, segment in enumerate(shuffled):
        chips = segment.get("chips") if isinstance(segment, Mapping) else None
        if not chips:
            continue
        updated: Dict[str, Any] = {}
        segment_updated = False
        for chip_id, chip in chips.items():
            following = _shuffle_chip(chip, random)
            updated[chip_id] = following
            if following is not chip:
                segment_updated = True
                changed = True
        if segment_updated:
            shuffled[index] = {**segment, "chips": updated}
    return shuffled, changed


def _resolve_segment_text(segment: Mapping[str, Any]) -> str:
    chips = segment.get("chips")
    content = _text(segment.get("content"))
    return rich_text_to_plain_text(content, chips) if chips else content


def _segment_input(segment: Mapping[str, Any], channel: str, prompt_index: int, segment_index: int) -> Dict[str, Any]:
    return {
        "channel": channel,
        "prompt_index": prompt_index,
        "segment_index": segment_index,
        "segment_type": "content",
        "text": apply_segment_affixes(segment, _resolve_segment_text(segment)),
        "is_disabled": segment.get("enabled") is False or bool(segment.get("isDisabled")),
        "name": segment.get("name"),
        "color": segment.get("color"),
        "description": segment.get("description"),
        "phrasebooks": [
            {
                "phrasebook_value_id": chip.get("valueId"),
                "category_path": chip.get("categoryPath"),
                "value": chip.get("value"),
            }
            for chip in (segment.get("chips") or {}).values()
        ],
    }


def build_segments_payload(tab: Mapping[str, Any], batch_prompts: bool) -> List[Dict[str, Any]]:
    entries = prompt_list(tab)
    sent = entries if batch_prompts else entries[:1]
    out: List[Dict[str, Any]] = []
    for prompt_index, entry in enumerate(sent):
        for index, segment in enumerate(entry.get("promptSegments") or []):
            out.append(_segment_input(segment, "positive", prompt_index, index))
        for index, segment in enumerate(entry.get("negativePromptSegments") or []):
            out.append(_segment_input(segment, "negative", prompt_index, index))
    return out

