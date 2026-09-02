"""Fooocus-style style packs: a list (or named lists) of {name, prompt, negative_prompt}."""
import json
import uuid
from typing import Any, Dict, List, Optional, Union

from src.features.prompt_database.importing.models import ParsedPrompt

PLACEHOLDER = "{prompt}"


def _entries_from(entries: Any) -> List[ParsedPrompt]:
    results: List[ParsedPrompt] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_name = entry.get("name")
        name = str(raw_name).strip() or None if raw_name else None
        prompt_text = str(entry.get("prompt") or "").strip()
        negative_text = str(entry.get("negative_prompt") or "").strip()
        if not prompt_text and not negative_text:
            continue
        group_id = uuid.uuid4().hex if prompt_text and negative_text else None
        if prompt_text:
            metadata: Dict[str, Any] = {"placeholder": PLACEHOLDER} if PLACEHOLDER in prompt_text else {}
            results.append(ParsedPrompt(
                text=prompt_text, usage_hint="positive", name=name, group_id=group_id, metadata=metadata,
            ))
        if negative_text:
            metadata = {"placeholder": PLACEHOLDER} if PLACEHOLDER in negative_text else {}
            results.append(ParsedPrompt(
                text=negative_text, usage_hint="negative", name=name, group_id=group_id, metadata=metadata,
            ))
    return results


def parse_style_json(data: Union[bytes, str], *, filename: Optional[str] = None) -> List[ParsedPrompt]:
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    payload = json.loads(text)
    if isinstance(payload, list):
        return _entries_from(payload)
    if isinstance(payload, dict):
        results: List[ParsedPrompt] = []
        for value in payload.values():
            if isinstance(value, list):
                results.extend(_entries_from(value))
        return results
    return []
