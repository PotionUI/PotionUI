import math
import re
from dataclasses import dataclass, field
from typing import Any, List, Tuple

_SNAP_TOLERANCE = 1e-6
_QUOTED = re.compile(r'"([^"]*)"|“([^”]*)”')
_RESOLUTION = re.compile(r"^\s*(\d+)\s*[xX×]\s*(\d+)\s*$")


@dataclass(frozen=True)
class RegionalItem:
    x: float
    y: float
    w: float
    h: float
    caption: str


@dataclass(frozen=True)
class RegionalPromptDocument:
    enabled: bool = False
    items: List[RegionalItem] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.items)


def _fraction(value: Any, name: str, index: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"regional_prompt.items[{index}].{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"regional_prompt.items[{index}].{name} must be finite")
    return min(1.0, max(0.0, number))


def normalize_regional_prompt(value: Any) -> RegionalPromptDocument:
    if value is None:
        return RegionalPromptDocument()
    if not isinstance(value, dict):
        raise ValueError("regional_prompt must be a mapping")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("regional_prompt.enabled must be a boolean")
    raw_items = value.get("items") or []
    if not isinstance(raw_items, list):
        raise ValueError("regional_prompt.items must be a list")
    items: List[RegionalItem] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"regional_prompt.items[{index}] must be a mapping")
        caption = raw.get("caption", "")
        if not isinstance(caption, str):
            raise ValueError(f"regional_prompt.items[{index}].caption must be a string")
        x, y, w, h = (_fraction(raw.get(key), key, index) for key in ("x", "y", "w", "h"))
        if w <= 0 or h <= 0:
            raise ValueError(f"regional_prompt.items[{index}] must have a positive width and height")
        items.append(RegionalItem(x=x, y=y, w=w, h=h, caption=caption))
    return RegionalPromptDocument(enabled=enabled, items=items)


def parse_resolution(value: Any) -> Tuple[int, int]:
    match = _RESOLUTION.match(str(value or ""))
    if match is None:
        raise ValueError(f"resolution must look like 1024x1024, got {value!r}")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise ValueError(f"resolution must be positive, got {value!r}")
    return width, height


def _snapped(edge: float) -> float:
    nearest = round(edge)
    return float(nearest) if abs(edge - nearest) <= _SNAP_TOLERANCE else edge


def pixel_box(item: RegionalItem, width: int, height: int) -> Tuple[int, int, int, int]:
    left = math.floor(_snapped(item.x * width))
    top = math.floor(_snapped(item.y * height))
    right = math.ceil(_snapped(min(1.0, item.x + item.w) * width))
    bottom = math.ceil(_snapped(min(1.0, item.y + item.h) * height))
    return left, top, right, bottom


def quoted_spans(caption: str) -> List[str]:
    spans = (straight or curly for straight, curly in _QUOTED.findall(caption))
    return [span.strip() for span in spans if span.strip()]
