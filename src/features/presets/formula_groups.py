from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.features.presets.templates import PresetTemplate, ModeTemplate, FormTemplate, default_form_name

FORMULA_INPUT_TYPES = frozenset({
    "image", "video", "audio", "media", "file", "prompt_timeline", "camera_shot", "seed",
    "alert", "markdown", "header",
})


@dataclass
class FormulaGroup:
    id: str
    label: str
    description: Optional[str] = None
    preselect: bool = True
    fields: List[str] = field(default_factory=list)


def _attr(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        return node.get(key)
    return getattr(node, key, None)


def is_value_field(node: Any) -> bool:
    name = _attr(node, "name")
    if not isinstance(name, str) or not name or "{{" in name:
        return False
    node_type = _attr(node, "type")
    if node_type == "@loop":
        return False
    return not isinstance(_attr(node, "children"), list) or node_type == "gate"


def iter_formula_fields(fields: Any, inherited: Optional[str] = None) -> Iterator[Tuple[Any, Optional[str]]]:
    for node in fields or []:
        if not isinstance(node, dict) and not hasattr(node, "type"):
            continue
        own = _attr(node, "formula")
        if own is None:
            group = inherited
        elif isinstance(own, str) and own:
            group = own
        else:
            group = None
        if is_value_field(node):
            yield node, group
        children = _attr(node, "children")
        if isinstance(children, list):
            yield from iter_formula_fields(children, group)


def formula_catalog(preset: PresetTemplate) -> Dict[str, Dict[str, Any]]:
    formulas = getattr(preset, "formulas", None)
    groups = formulas.get("groups") if isinstance(formulas, dict) else None
    return dict(groups) if isinstance(groups, dict) else {}


def _select_form(mode_data: ModeTemplate, form_name: Optional[str]) -> Optional[FormTemplate]:
    if not mode_data.forms:
        return None
    target = form_name or default_form_name(mode_data)
    for form in mode_data.forms:
        if form.name == target:
            return form
    return None if form_name else mode_data.forms[0]


def resolve_formula_groups(
    preset: PresetTemplate, mode: str, form_name: Optional[str] = None
) -> List[FormulaGroup]:
    catalog = formula_catalog(preset)
    mode_data = (preset.modes or {}).get(mode)
    if not catalog or mode_data is None:
        return []
    form = _select_form(mode_data, form_name)
    if form is None:
        return []

    members: Dict[str, List[str]] = {}
    assigned: set = set()
    for node, group in iter_formula_fields(form.fields):
        name = _attr(node, "name")
        if group not in catalog or name in assigned or _attr(node, "type") in FORMULA_INPUT_TYPES:
            continue
        assigned.add(name)
        members.setdefault(group, []).append(name)

    return [
        FormulaGroup(
            id=group_id,
            label=spec.get("label") or group_id,
            description=spec.get("description"),
            preselect=spec.get("preselect", True) is not False,
            fields=members[group_id],
        )
        for group_id, spec in catalog.items()
        if group_id in members
    ]
