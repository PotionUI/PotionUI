import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, FrozenSet, Optional

MODE_NAMES_FILE = Path(__file__).with_name("mode_names.json")
ICON_LIBRARY_FILE = Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "utils" / "IconLibrary.ts"

_MODE_NAMES: Dict[str, Dict[str, str]] = json.loads(MODE_NAMES_FILE.read_text(encoding="utf-8"))
MODE_LABELS: Dict[str, str] = {key: entry["label"] for key, entry in _MODE_NAMES.items()}
MODE_ICONS: Dict[str, str] = {key: entry["icon"] for key, entry in _MODE_NAMES.items() if entry.get("icon")}


def fallback_mode_label(key: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in re.split(r"[_\-\s]+", key) if word)


def is_known_mode(key: str) -> bool:
    return key.lower() in MODE_LABELS


def mode_display_name(key: str, override: Optional[str] = None) -> str:
    if override and override.strip():
        return override.strip()
    return MODE_LABELS.get(key.lower()) or fallback_mode_label(key) or key


def mode_display_icon(key: str, override: Optional[str] = None) -> Optional[str]:
    if override and override.strip():
        return override.strip()
    return MODE_ICONS.get(key.lower())


@lru_cache(maxsize=1)
def known_icon_names(icon_library: Path = ICON_LIBRARY_FILE) -> Optional[FrozenSet[str]]:
    try:
        text = icon_library.read_text(encoding="utf-8")
        body = text[text.index("export const iconPaths"):text.index("\n};")]
    except (OSError, ValueError):
        return None
    keys = re.findall(r"^\s*(?:'([^']+)'|([A-Za-z0-9_]+))\s*:", body, re.M)
    return frozenset(quoted or bare for quoted, bare in keys)
