import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

SETTING_KEY = "editor_expand_formats"
MAX_RATIO = 8.0
MAX_HOUSE_FORMATS = 40
LABEL_MAX = 40
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

BUILT_IN: Tuple[Dict[str, Any], ...] = (
    {"id": "square", "label": "Square", "ratio": "1:1"},
    {"id": "portrait", "label": "Portrait", "ratio": "4:5"},
    {"id": "story", "label": "Story", "ratio": "9:16"},
    {"id": "landscape", "label": "Landscape", "ratio": "16:9"},
    {"id": "wide_banner", "label": "Wide banner", "ratio": "3:1"},
)


def parse_ratio(value: Any) -> Optional[Tuple[int, int]]:
    try:
        left, right = (float(part) for part in str(value).split(":"))
    except ValueError:
        return None
    if left <= 0 or right <= 0 or not 1 / MAX_RATIO <= left / right <= MAX_RATIO:
        return None
    if left != int(left) or right != int(right):
        return None
    return int(left), int(right)


def house_format(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    label = str(raw.get("label") or "").strip()[:LABEL_MAX]
    ratio = parse_ratio(raw.get("ratio"))
    ident = str(raw.get("id") or "").strip().lower()
    if not label or ratio is None or not ID_PATTERN.match(ident):
        return None
    group = raw.get("group_id")
    return {
        "id": f"house.{ident}",
        "label": label,
        "ratio": f"{ratio[0]}:{ratio[1]}",
        "source": "house",
        "group_id": group if isinstance(group, str) and group else None,
    }


class ExpandFormats:
    def __init__(self, read_setting: Callable[[], Any], groups_of: Callable[[str], Iterable[str]]):
        self._read = read_setting
        self._groups_of = groups_of

    def for_user(self, user: Any) -> List[Dict[str, Any]]:
        formats = [{**entry, "source": "built_in", "group_id": None} for entry in BUILT_IN]
        raw = self._read()
        if not isinstance(raw, list):
            return formats
        member_of = set(self._groups_of(user.id))
        seen = {entry["id"] for entry in formats}
        for item in raw[:MAX_HOUSE_FORMATS]:
            entry = house_format(item)
            if entry is None or entry["id"] in seen:
                continue
            if entry["group_id"] is not None and entry["group_id"] not in member_of:
                continue
            seen.add(entry["id"])
            formats.append(entry)
        return formats
