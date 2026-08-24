"""Wire-shape normalization for tool_call arguments across LLM providers.

The canonical in-process shape for a tool_call's ``function.arguments`` is a
JSON OBJECT (dict). Providers disagree on the wire: the OpenAI
``/chat/completions`` convention sends arguments as a JSON STRING, while
Ollama's native ``/api/chat`` requires an OBJECT and returns
``400 "Value looks like object, but can't find closing '}' symbol"`` when handed
a string.

Each client normalizes to its own wire shape at the request boundary via these
helpers, so an assistant tool_call echoed back into history is always the shape
that provider expects — regardless of which path produced it (a provider
response, a rescue repair, a forced call, or an approval replay).
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def arguments_to_object(arguments: Any) -> Dict[str, Any]:
    """Coerce tool_call arguments to a JSON object (Ollama's wire shape)."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "[tool_call_shape] could not decode string arguments to object: %r",
                arguments[:200],
            )
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def arguments_to_json_string(arguments: Any) -> str:
    """Coerce tool_call arguments to a JSON string (OpenAI's wire shape)."""
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return "{}"
    try:
        return json.dumps(arguments)
    except (TypeError, ValueError):
        return "{}"


def normalize_tool_calls(
    tool_calls: Optional[List[Dict[str, Any]]], as_object: bool
) -> Optional[List[Dict[str, Any]]]:
    """Return a copy of *tool_calls* with each ``function.arguments`` coerced.

    ``as_object`` picks the wire shape: True → JSON object (Ollama), False → JSON
    string (OpenAI). Entries are copied shallowly so the caller's stored history
    is never mutated in place.
    """
    if not tool_calls:
        return tool_calls
    convert = arguments_to_object if as_object else arguments_to_json_string
    normalized: List[Dict[str, Any]] = []
    for tc in tool_calls:
        fn = tc.get("function")
        if isinstance(fn, dict) and "arguments" in fn:
            tc = {**tc, "function": {**fn, "arguments": convert(fn.get("arguments"))}}
        normalized.append(tc)
    return normalized
