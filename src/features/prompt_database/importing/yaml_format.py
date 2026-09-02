"""dynamicprompts wildcard YAML: nested mappings whose leaves are prompt strings."""
import yaml
from typing import List, Optional, Union

from src.features.prompt_database.importing.models import ParsedPrompt


def _walk(node, path: List[str], results: List[ParsedPrompt]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, path + [str(key)], results)
        return
    if isinstance(node, list):
        for item in node:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    results.append(ParsedPrompt(text=text, usage_hint="positive", tags=list(path)))
            else:
                _walk(item, path, results)
        return
    if isinstance(node, str):
        text = node.strip()
        if text:
            results.append(ParsedPrompt(text=text, usage_hint="positive", tags=list(path)))


def parse_wildcard_yaml(data: Union[bytes, str], *, filename: Optional[str] = None) -> List[ParsedPrompt]:
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    payload = yaml.safe_load(text)
    results: List[ParsedPrompt] = []
    if payload is None:
        return results
    _walk(payload, [], results)
    return results
