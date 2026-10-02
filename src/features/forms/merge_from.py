from typing import Any, Dict, Iterable, List, Mapping

from src.features.prompt.markers import RESOURCE_MARKER_RE


def _as_list(value: Any) -> List[Any]:
    if value is None or value == "" or value == []:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def merge_from_aliases(fields: Iterable[Any]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for field in fields:
        name = getattr(field, "name", None)
        sources = getattr(field, "merge_from", None)
        if name and isinstance(sources, (list, tuple)):
            for key in sources:
                if isinstance(key, str) and key:
                    aliases.setdefault(key, name)
        children = getattr(field, "children", None)
        if isinstance(children, list):
            for old, new in merge_from_aliases(children).items():
                aliases.setdefault(old, new)
    return aliases


def mode_merge_from_aliases(preset_template: Any, mode: Any) -> Dict[str, str]:
    mode_data = (getattr(preset_template, "modes", None) or {}).get(mode)
    aliases: Dict[str, str] = {}
    for form in getattr(mode_data, "forms", None) or []:
        for old, new in merge_from_aliases(form.fields or []).items():
            aliases.setdefault(old, new)
    return aliases


def rewrite_merged_markers(value: Any, aliases: Mapping[str, str]) -> Any:
    if not aliases:
        return value
    if isinstance(value, str):
        if "@[" not in value:
            return value
        return RESOURCE_MARKER_RE.sub(
            lambda match: f"@[{aliases.get(match.group(1), match.group(1))}:{match.group(2)}]"
            if match.group(1) in aliases else match.group(0),
            value,
        )
    if isinstance(value, list):
        return [rewrite_merged_markers(item, aliases) for item in value]
    if isinstance(value, dict):
        rewritten = {key: rewrite_merged_markers(item, aliases) for key, item in value.items()}
        field = rewritten.get("field")
        if isinstance(field, str) and field in aliases and "item_key" in rewritten:
            rewritten["field"] = aliases[field]
        return rewritten
    return value


def apply_merge_from(fields: Iterable[Any], data: Mapping[str, Any]) -> Dict[str, Any]:
    fields = list(fields)
    merged = dict(data)
    for field in fields:
        sources = getattr(field, "merge_from", None)
        name = getattr(field, "name", None)
        if not sources or not name:
            continue
        present = [key for key in sources if key in merged]
        if not present:
            continue
        combined = _as_list(merged.get(name))
        for key in sources:
            combined.extend(_as_list(merged.get(key)))
        for key in sources:
            merged.pop(key, None)
        merged[name] = combined
    aliases = {
        key: field.name
        for field in fields
        if getattr(field, "name", None) and getattr(field, "merge_from", None)
        for key in field.merge_from
    }
    return rewrite_merged_markers(merged, aliases)
