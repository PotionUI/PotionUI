import logging
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)

KIND_EDITOR = "editor"
KIND_MCP = "mcp"
KIND_CHAT = "chat"
KIND_TAB = "tab"

FILTER_VALUES = (KIND_EDITOR, KIND_MCP, KIND_CHAT, KIND_TAB)

KeyNameLookup = Callable[[str, Iterable[str]], Mapping[str, str]]


def mcp_origin(key_id: str, key_name: str) -> Dict[str, Any]:
    return {"kind": KIND_MCP, "key_id": key_id, "key_name": key_name}


def chat_origin() -> Dict[str, Any]:
    return {"kind": KIND_CHAT}


def _key_id(origin: Any) -> Optional[str]:
    if isinstance(origin, dict) and origin.get("kind") == KIND_MCP:
        key_id = origin.get("key_id")
        return key_id if isinstance(key_id, str) and key_id else None
    return None


def present_origins(items: List[Dict[str, Any]], lookup: Optional[KeyNameLookup], owner_id: Optional[str] = None) -> None:
    owners: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        owner = owner_id or item.get("user_id")
        if owner and _key_id(item.get("origin")):
            owners.setdefault(owner, []).append(item)
    for owner, group in owners.items():
        current: Mapping[str, str] = {}
        if lookup is not None:
            try:
                current = lookup(owner, sorted({_key_id(item["origin"]) for item in group}))
            except Exception:
                logger.exception("could not resolve the current names of the API keys behind generations")
        for item in group:
            origin = item["origin"]
            item["origin"] = {**origin, "key_current_name": current.get(origin["key_id"])}
