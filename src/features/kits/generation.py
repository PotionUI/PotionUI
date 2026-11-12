from typing import Any, Dict, List, Optional, Tuple

from src.features.kits.citations import has_kit_cite
from src.features.kits.expansion import KitExpander, KitExpansion, field_policies
from src.features.prompt.resources import mode_prompt_resources

DIRECTOR_TEXT_KEYS = ("prompt", "negative_prompt")


def _director_slots(form_data: Dict[str, Any]) -> List[Tuple[int, str]]:
    document = form_data.get("video_director")
    segments = document.get("segments") if isinstance(document, dict) else None
    if not isinstance(segments, list):
        return []
    return [
        (index, key) for index, segment in enumerate(segments) if isinstance(segment, dict)
        for key in DIRECTOR_TEXT_KEYS if isinstance(segment.get(key), str)
    ]


def _with_director_texts(form_data: Dict[str, Any], slots: List[Tuple[int, str]], texts: List[str]) -> Dict[str, Any]:
    changed = [
        (index, key, text) for (index, key), text in zip(slots, texts)
        if form_data["video_director"]["segments"][index].get(key) != text
    ]
    if not changed:
        return form_data
    document = dict(form_data["video_director"])
    segments = list(document["segments"])
    for index, key, text in changed:
        segments[index] = {**segments[index], key: text}
    document["segments"] = segments
    return {**form_data, "video_director": document}


def expand_request(
    expander: KitExpander,
    owner_id: str,
    request: Any,
    preset_template: Any,
    mode: Optional[str],
    loaded: Dict[str, List[str]],
) -> KitExpansion:
    form_data = request.form_data if isinstance(request.form_data, dict) else {}
    prompts = getattr(request, "prompts", None)
    prompts = prompts if isinstance(prompts, list) else []
    segments = getattr(request, "segments", None)
    segments = segments if isinstance(segments, list) else []
    prompt_slots = [(pair, channel) for pair in prompts for channel in ("positive", "negative") if isinstance(getattr(pair, channel, None), str)]
    segment_slots = [segment for segment in segments if isinstance(getattr(segment, "text", None), str)]
    director_slots = _director_slots(form_data)
    texts = (
        [getattr(pair, channel) for pair, channel in prompt_slots]
        + [segment.text for segment in segment_slots]
        + [form_data["video_director"]["segments"][index][key] for index, key in director_slots]
    )
    if not loaded and not any(has_kit_cite(text) for text in texts):
        return KitExpansion(form_data=request.form_data, texts=texts)
    policies = field_policies(mode_prompt_resources(preset_template, mode))
    expansion = expander.expand(owner_id, policies, form_data, loaded, texts)
    if not expansion.touched and expansion.texts == texts:
        return expansion
    cursor = 0
    for pair, channel in prompt_slots:
        setattr(pair, channel, expansion.texts[cursor])
        cursor += 1
    for segment in segment_slots:
        segment.text = expansion.texts[cursor]
        cursor += 1
    expansion.form_data = _with_director_texts(expansion.form_data, director_slots, expansion.texts[cursor:])
    request.form_data = expansion.form_data
    return expansion
