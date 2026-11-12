import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

FORM_MEDIA_TYPES = frozenset({"image", "video", "audio", "media"})
DEFAULT_FIELD_CAP = 8
_warned: set = set()


@dataclass(frozen=True)
class FormLimit:
    total: Optional[int] = None
    by_kind: Mapping[str, int] = field(default_factory=dict)


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _limit_of(spec: Any) -> Optional[FormLimit]:
    if getattr(spec, "type", None) not in FORM_MEDIA_TYPES:
        return None
    config = getattr(spec, "configuration", None)
    config = config if isinstance(config, dict) else {}
    total = _count(config.get("max_items"))
    by_kind = config.get("max_items_by_kind")
    kinds = {
        kind: count for kind, value in (by_kind.items() if isinstance(by_kind, dict) else ())
        if (count := _count(value)) is not None
    }
    if total is None and not kinds and not config.get("multi"):
        total = 1
    return FormLimit(total, kinds)


def _tighter(first: Optional[int], second: Optional[int]) -> Optional[int]:
    if first is None or second is None:
        return first if second is None else second
    return min(first, second)


def _merge(first: FormLimit, second: FormLimit) -> FormLimit:
    kinds = dict(first.by_kind)
    for kind, count in second.by_kind.items():
        kinds[kind] = min(count, kinds.get(kind, count))
    return FormLimit(_tighter(first.total, second.total), kinds)


def _walk(fields: Any, found: Dict[str, FormLimit]) -> None:
    for spec in fields if isinstance(fields, list) else ():
        name = getattr(spec, "name", None)
        limit = _limit_of(spec) if name else None
        if limit is not None:
            found[name] = _merge(found[name], limit) if name in found else limit
        _walk(getattr(spec, "children", None), found)


def form_limits(preset_template: Any, mode: Optional[str]) -> Dict[str, FormLimit]:
    mode_data = (getattr(preset_template, "modes", None) or {}).get(mode)
    found: Dict[str, FormLimit] = {}
    for form in getattr(mode_data, "forms", None) or []:
        _walk(getattr(form, "fields", None), found)
    return found


def warn_default_cap(name: str) -> None:
    if name not in _warned:
        _warned.add(name)
        logger.warning("No limit is declared for kit items in field %r: capped at %d", name, DEFAULT_FIELD_CAP)
