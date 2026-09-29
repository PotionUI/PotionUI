from typing import Any, Dict, List, Optional

PREVIEW_LIMIT = 80



def _is_mode_keyed(data: Any) -> bool:
    return isinstance(data, dict) and bool(data) and all(isinstance(v, dict) for v in data.values())


def _slots(data: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(data, dict):
        return {}
    if _is_mode_keyed(data):
        return data
    return {"": data}


def _resolve_chips(content: str, chips: Dict[str, Any]) -> str:
    for chip in chips.values():
        if not isinstance(chip, dict):
            continue
        path = chip.get("categoryPath")
        if not path:
            continue
        value = str(chip.get("value", ""))
        for pattern in (f"#[{path}]", f"#{path}"):
            if pattern in content:
                content = content.replace(pattern, value, 1)
                break
    return content


def _segments_text(segments: Any) -> str:
    parts: List[str] = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        enabled = segment.get("enabled")
        if enabled is False or (enabled is None and segment.get("isDisabled")):
            continue
        content = str(segment.get("content") or "")
        chips = segment.get("chips")
        if isinstance(chips, dict) and chips:
            content = _resolve_chips(content, chips)
        body = content.strip()
        if not body:
            continue
        parts.append(f"{segment.get('prefix') or ''}{body}{segment.get('suffix') or ''}")
    return " ".join(parts).strip()


def _channel_text(slot: Dict[str, Any], text_key: str, segment_keys: tuple) -> str:
    for key in segment_keys:
        segments = slot.get(key)
        if isinstance(segments, list) and segments:
            text = _segments_text(segments)
            if text:
                return text
    value = slot.get(text_key)
    return value.strip() if isinstance(value, str) else ""


def positive_text(slot: Dict[str, Any]) -> str:
    return _channel_text(slot, "prompt", ("promptSegments", "segments"))


def negative_text(slot: Dict[str, Any]) -> str:
    return _channel_text(slot, "negativePrompt", ("negativePromptSegments", "negativeSegments"))


def trim_preview(text: str, limit: int = PREVIEW_LIMIT) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if not text[limit].isspace():
        boundary = cut.rfind(" ")
        if boundary > 0:
            cut = cut[:boundary]
    return cut.rstrip(" ,.;:-") + "…"


def _changed_modes(data: Any, previous: Optional[Any]) -> List[str]:
    slots = _slots(data)
    if previous is None:
        return list(slots)
    before = _slots(previous)
    return [mode for mode, slot in slots.items() if before.get(mode) != slot]


def prompt_preview(data: Any, previous: Optional[Any] = None) -> str:
    slots = _slots(data)
    if not slots:
        return ""
    candidates = _changed_modes(data, previous) or list(slots)
    ordered = candidates + [m for m in slots if m not in candidates]
    for mode in ordered:
        text = positive_text(slots[mode])
        if text:
            return trim_preview(text)
    return ""


def _form(slot: Dict[str, Any]) -> Dict[str, Any]:
    form = slot.get("formData")
    return form if isinstance(form, dict) else {}


def _slot_labels(current: Dict[str, Any], previous: Dict[str, Any]) -> List[str]:
    labels: List[str] = []
    if positive_text(current) != positive_text(previous) or current.get("promptTabs") != previous.get("promptTabs"):
        labels.append("prompt")
    if negative_text(current) != negative_text(previous):
        labels.append("negative prompt")
    return labels


def _slot_fields(current: Dict[str, Any], previous: Dict[str, Any]) -> set:
    form, old_form = _form(current), _form(previous)
    return {key for key in set(form) | set(old_form) if form.get(key) != old_form.get(key)}


_ORDER = ["prompt", "negative prompt", "mode"]


def changes_between(data: Any, previous: Any) -> List[str]:
    slots, before = _slots(data), _slots(previous)
    found = set()
    if set(slots) != set(before):
        found.add("mode")
    for mode in set(slots) & set(before):
        if slots[mode] != before[mode]:
            found.update(_slot_labels(slots[mode], before[mode]))
            if slots[mode].get("selectedMode") != before[mode].get("selectedMode"):
                found.add("mode")
    return [label for label in _ORDER if label in found]


def changed_fields_between(data: Any, previous: Any) -> List[str]:
    slots, before = _slots(data), _slots(previous)
    keys: set = set()
    for mode in set(slots) & set(before):
        keys |= _slot_fields(slots[mode], before[mode])
    return sorted(keys)
