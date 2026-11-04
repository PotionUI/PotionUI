import hashlib
import json
from typing import Any, Mapping, Optional

ROOT_INPUT = "-"
INVOCATION_KEY_PREFIX = "elixir:"
HISTORY_REF_KEYS = ("kind", "generation_id", "file_id")
LIBRARY_REF_KEYS = ("kind", "item_id")


def media_ref(value: Mapping[str, Any]) -> dict:
    kind = value.get("kind")
    keys = HISTORY_REF_KEYS if kind == "history" else LIBRARY_REF_KEYS if kind == "library" else None
    if keys is None:
        raise ValueError("A media reference must be a history or library reference")
    ref = {key: value.get(key) for key in keys}
    for key in keys[1:]:
        if not isinstance(ref[key], str) or not ref[key]:
            raise ValueError(f"A {kind} reference needs {key}")
    return ref


def asset_key(value: Mapping[str, Any]) -> str:
    if value.get("kind") == "text":
        text_id = value.get("text_id")
        if not isinstance(text_id, str) or not text_id:
            raise ValueError("A text reference needs text_id")
        return f"text:{text_id}"
    ref = media_ref(value)
    if ref["kind"] == "history":
        return f"history:{ref['generation_id']}:{ref['file_id']}"
    return f"library:{ref['item_id']}"


def invocation_key(
    run_id: str,
    node_id: str,
    edge_id: Optional[str],
    input_key: Optional[str],
    attempt: int,
) -> str:
    if not run_id or not node_id:
        raise ValueError("An invocation needs a run and a node")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise ValueError("An attempt number starts at 1")
    payload = json.dumps(
        [run_id, node_id, edge_id or "", input_key or ROOT_INPUT, attempt],
        separators=(",", ":"),
    )
    return INVOCATION_KEY_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()
