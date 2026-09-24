import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.features.forms.binding import FormBindingError
from src.features.presets.schema import PROMPT_RESOURCE_INDEX_PLACEHOLDER

RESOURCE_MARKER_RE = re.compile(r"@\[([A-Za-z_][A-Za-z0-9_-]*):([^\]\r\n]+)\]")
MEDIA_ITEM_KEY_FIELDS = ("relative_path", "path", "url")


def media_item_keys(item: Any) -> Tuple[str, ...]:
    if isinstance(item, str):
        return (item,) if item else ()
    if isinstance(item, dict):
        return tuple(
            value for value in (item.get(key) for key in MEDIA_ITEM_KEY_FIELDS)
            if isinstance(value, str) and value
        )
    return ()


def _field_items(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _item_position(value: Any, item_key: str) -> Optional[int]:
    for index, item in enumerate(_field_items(value), start=1):
        if item_key in media_item_keys(item):
            return index
    return None


def mode_prompt_resources(preset_template: Any, mode: Optional[str]) -> List[Dict[str, Any]]:
    return list(preset_template.prompt_resources.get(mode, []))


def resolve_prompt_resources(
    text: str,
    resources: Sequence[Mapping[str, Any]],
    form_values: Mapping[str, Any],
) -> Tuple[str, Dict[str, List[str]]]:
    if not text or "@[" not in text:
        return text, {}
    mapped = {entry["field"]: entry for entry in resources}
    problems: Dict[str, List[str]] = {}

    def note(field: str, message: str) -> None:
        messages = problems.setdefault(field, [])
        if message not in messages:
            messages.append(message)

    def replace(match: "re.Match[str]") -> str:
        field, item_key = match.group(1), match.group(2)
        spec = mapped.get(field)
        if spec is None:
            note(field, f"the prompt references '{field}', which this mode's prompt cannot reference")
            return match.group(0)
        position = _item_position(form_values.get(field), item_key)
        if position is None:
            note(field, f"the prompt references an item that was removed from this field ({item_key})")
            return match.group(0)
        return spec["token"].replace(PROMPT_RESOURCE_INDEX_PLACEHOLDER, str(position))

    return RESOURCE_MARKER_RE.sub(replace, text), problems


def _merge_problems(into: Dict[str, List[str]], found: Dict[str, List[str]]) -> None:
    for field, messages in found.items():
        bucket = into.setdefault(field, [])
        bucket.extend(message for message in messages if message not in bucket)


def resolve_prompt_pairs(
    prompts: Iterable[Any],
    resources: Sequence[Mapping[str, Any]],
    form_values: Mapping[str, Any],
) -> None:
    problems: Dict[str, List[str]] = {}
    for pair in prompts or []:
        for channel in ("positive", "negative"):
            text = getattr(pair, channel, None)
            if not isinstance(text, str):
                continue
            resolved, found = resolve_prompt_resources(text, resources, form_values)
            _merge_problems(problems, found)
            setattr(pair, channel, resolved)
    if problems:
        errors = [f"{field}: {message}" for field, messages in problems.items() for message in messages]
        raise FormBindingError(errors, field_errors=problems)


def resolve_segment_texts(
    segments: Iterable[Any],
    resources: Sequence[Mapping[str, Any]],
    form_values: Mapping[str, Any],
) -> None:
    for segment in segments or []:
        text = getattr(segment, "text", None)
        if isinstance(text, str):
            segment.text = resolve_prompt_resources(text, resources, form_values)[0]


def _has_marker(text: Any) -> bool:
    return isinstance(text, str) and RESOURCE_MARKER_RE.search(text) is not None


def resolve_generation_prompts(
    preset_template: Any,
    mode: Optional[str],
    prompts: Optional[List[Any]],
    segments: Optional[List[Any]],
    form_values: Mapping[str, Any],
) -> None:
    prompts = prompts if isinstance(prompts, list) else []
    segments = segments if isinstance(segments, list) else []
    texts = [getattr(pair, channel, None) for pair in prompts for channel in ("positive", "negative")]
    texts.extend(getattr(segment, "text", None) for segment in segments)
    if not any(_has_marker(text) for text in texts):
        return
    resources = preset_template.prompt_resources.get(mode, [])
    resolve_prompt_pairs(prompts, resources, form_values)
    resolve_segment_texts(segments, resources, form_values)
