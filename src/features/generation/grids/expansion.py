import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.features.forms.binding import cascade_reactions
from src.features.generation.dto import GenerationRequest
from src.features.generation.grids.dto import PROMPT_AXIS_FIELD, Axis

MAX_SEED = 2 ** 32 - 1
MODEL_FIELD_TYPES = frozenset({"model", "models"})


@dataclass
class Cell:
    x: int
    y: int
    axis_values: Dict[str, Any]
    request: GenerationRequest
    seed: Optional[int]
    model_key: Tuple[str, ...] = field(default_factory=tuple)


def cell_key(x: int, y: int) -> str:
    return f"{x},{y}"


def axes_of(x_axis: Axis, y_axis: Optional[Axis]) -> List[Axis]:
    return [x_axis] + ([y_axis] if y_axis is not None else [])


def grid_shape(x_axis: Axis, y_axis: Optional[Axis]) -> Tuple[int, int]:
    return len(x_axis.values), len(y_axis.values) if y_axis is not None else 1


def positions(x_axis: Axis, y_axis: Optional[Axis]) -> List[Tuple[int, int]]:
    cols, rows = grid_shape(x_axis, y_axis)
    return [(x, y) for y in range(rows) for x in range(cols)]


def display_value(entry) -> str:
    if entry.label:
        return entry.label
    value = entry.value
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return "" if value is None else str(value)


def seed_field_names(field_index: Mapping[str, Any]) -> List[str]:
    names = [name for name, spec in field_index.items() if getattr(spec, "type", None) == "seed"]
    return names or ["seed"]


def usable_seed(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        seed = int(value)
    except (TypeError, ValueError):
        return None
    return seed if seed >= 0 else None


def apply_lora_value(rows: Any, value: Mapping[str, Any]) -> List[Any]:
    ref = value.get("lora")
    if isinstance(ref, Mapping):
        ref = ref.get("model") or ref.get("id")
    strength = float(value.get("strength", 1.0))
    updated = [dict(row) if isinstance(row, Mapping) else row for row in (rows if isinstance(rows, list) else [])]
    for row in updated:
        if isinstance(row, dict) and row.get("model") == ref:
            row["strength"] = strength
            return updated
    updated.append({"model": ref, "strength": strength})
    return updated


def replace_prompt_text(request: GenerationRequest, value: Mapping[str, Any]) -> None:
    find = value.get("find")
    replace = value.get("replace")
    if not isinstance(find, str) or not find or replace is None:
        return
    for pair in request.prompts or []:
        if isinstance(pair.positive, str):
            pair.positive = pair.positive.replace(find, str(replace))
    for segment in request.segments or []:
        if segment.channel == "positive" and not segment.is_disabled and isinstance(segment.text, str):
            segment.text = segment.text.replace(find, str(replace))


def apply_axis_value(
    request: GenerationRequest, form_data: Dict[str, Any], axis: Axis, value: Any
) -> None:
    if axis.field == PROMPT_AXIS_FIELD:
        if isinstance(value, Mapping):
            replace_prompt_text(request, value)
        return
    if isinstance(value, Mapping) and "lora" in value and (axis.type == "lora_picker" or "strength" in value):
        form_data[axis.field] = apply_lora_value(form_data.get(axis.field), value)
        return
    form_data[axis.field] = value


def is_model_axis(axis: Axis, field_index: Mapping[str, Any]) -> bool:
    if axis.type in MODEL_FIELD_TYPES:
        return True
    spec = field_index.get(axis.field)
    return getattr(spec, "type", None) in MODEL_FIELD_TYPES


def cell_idempotency_key(key: Optional[str], x: int, y: int) -> Optional[str]:
    if not key:
        return None
    return f"{key[:150]}:grid:{x}:{y}"


def resolve_seeds(
    cells: List[Tuple[int, int]],
    base_form_data: Mapping[str, Any],
    primary_field: str,
    lock_seed: bool,
    rng: random.Random,
    stored: Optional[Mapping[str, int]],
) -> Dict[str, int]:
    seeds: Dict[str, int] = {}
    shared = usable_seed(base_form_data.get(primary_field))
    if lock_seed and shared is None:
        shared = rng.randint(0, MAX_SEED)
    for x, y in cells:
        key = cell_key(x, y)
        if stored and key in stored:
            seeds[key] = int(stored[key])
        elif lock_seed:
            seeds[key] = shared
        else:
            seeds[key] = rng.randint(0, MAX_SEED)
    return seeds


def expand_cells(
    base_request: GenerationRequest,
    x_axis: Axis,
    y_axis: Optional[Axis],
    lock_seed: bool,
    field_index: Mapping[str, Any],
    *,
    preset_id: str,
    mode: str,
    rng: Optional[random.Random] = None,
    stored_seeds: Optional[Mapping[str, int]] = None,
    only: Optional[List[Tuple[int, int]]] = None,
    quantity_fields: Iterable[str] = (),
) -> List[Cell]:
    rng = rng or random.Random()
    axes = axes_of(x_axis, y_axis)
    axis_fields = {axis.field for axis in axes if axis.field != PROMPT_AXIS_FIELD}
    seed_names = seed_field_names(field_index)
    primary_seed = seed_names[0]
    wanted = only if only is not None else positions(x_axis, y_axis)
    seeds = resolve_seeds(wanted, base_request.form_data or {}, primary_seed, lock_seed, rng, stored_seeds)
    model_flags = [is_model_axis(axis, field_index) for axis in axes]
    cells: List[Cell] = []
    for x, y in wanted:
        request = base_request.model_copy(deep=True)
        form_data = dict(request.form_data or {})
        entries = [x_axis.values[x]] + ([y_axis.values[y]] if y_axis is not None else [])
        axis_values: Dict[str, Any] = {}
        for axis, entry in zip(axes, entries):
            apply_axis_value(request, form_data, axis, entry.value)
            axis_values[axis.field] = display_value(entry)
        if axis_fields:
            cascade_reactions(field_index, form_data, axis_fields, preset_id=preset_id, mode=mode)
        seed = seeds[cell_key(x, y)]
        for name in seed_names:
            if name not in axis_fields:
                form_data[name] = seed
        for name in quantity_fields:
            if name in form_data or name in field_index:
                form_data[name] = 1
        if request.prompts and len(request.prompts) > 1:
            request.prompts = request.prompts[:1]
        request.form_data = form_data
        request.idempotency_key = cell_idempotency_key(base_request.idempotency_key, x, y)
        cell_seed = usable_seed(form_data.get(primary_seed))
        model_key = tuple(
            json.dumps(entry.value, sort_keys=True, default=str)
            for is_model, entry in zip(model_flags, entries)
            if is_model
        )
        cells.append(Cell(x, y, axis_values, request, cell_seed if cell_seed is not None else seed, model_key))
    return cells


def queue_order(cells: List[Cell]) -> List[Cell]:
    first_seen: Dict[Tuple[str, ...], int] = {}
    for cell in sorted(cells, key=lambda c: (c.y, c.x)):
        first_seen.setdefault(cell.model_key, len(first_seen))
    return sorted(cells, key=lambda c: (first_seen[c.model_key], c.y, c.x))
