from typing import Any, Dict, Optional

META_KEY_LIMITS: Dict[str, int] = {"label": 80, "unit": 16, "type": 40}


def normalize_param_meta(raw: Any) -> Optional[Dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    meta: Dict[str, str] = {}
    for key, limit in META_KEY_LIMITS.items():
        value = raw.get(key)
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value:
            meta[key] = value[:limit]
    return meta or None
