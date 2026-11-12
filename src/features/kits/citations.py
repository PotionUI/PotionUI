import re
from dataclasses import dataclass
from typing import Optional

from src.features.prompt.markers import RESOURCE_MARKER_RE

KEY_PREFIX = "kit:"
PATH_CITE_RE = re.compile(r"(?<![\w@])@kits\.([a-z][a-z0-9_]{0,31})(?:\.([a-z][a-z0-9_]{0,31}))?")
STABLE_RE = re.compile(r"^(kt_[0-9a-f]{8})(?:/(it_[0-9a-f]{8}))?$")


@dataclass(frozen=True)
class StableRef:
    kit_id: str
    item_id: Optional[str] = None

    @property
    def key(self) -> str:
        return item_key(self.kit_id, self.item_id)


def item_key(kit_id: str, item_id: Optional[str] = None) -> str:
    return f"{KEY_PREFIX}{kit_id}" + (f"/{item_id}" if item_id else "")


def marker(field: str, kit_id: str, item_id: Optional[str] = None) -> str:
    return f"@[{field}:{item_key(kit_id, item_id)}]"


def parse_key(key: str) -> Optional[StableRef]:
    if not isinstance(key, str) or not key.startswith(KEY_PREFIX):
        return None
    found = STABLE_RE.match(key[len(KEY_PREFIX):])
    return StableRef(found.group(1), found.group(2)) if found else None


def parse_stable(value: str) -> Optional[StableRef]:
    found = STABLE_RE.match((value or "").strip())
    return StableRef(found.group(1), found.group(2)) if found else None


def has_kit_cite(text) -> bool:
    if not isinstance(text, str):
        return False
    if "@kits." in text and PATH_CITE_RE.search(text):
        return True
    return "@[" in text and any(
        match.group(2).startswith(KEY_PREFIX) for match in RESOURCE_MARKER_RE.finditer(text)
    )
