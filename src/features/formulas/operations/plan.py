import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.features.formulas.collaborators import FormulaCollaborators
from src.features.formulas.dto import PlanFormulaRequest
from src.features.formulas.errors import FormulaError
from src.features.formulas.operations.write import get_formula
from src.features.formulas.signatures import (
    MODEL_TYPES,
    NUMERIC_TYPES,
    OPTION_TYPES,
    companion_accepted,
    configuration,
    numeric_bounds,
    option_values,
    owning_field,
)
from src.features.formulas.sources import FormUnavailable, LoadedForm
from src.features.models.form_refs import is_model_ref, model_id_of
from src.platform.security.user import User

REASONS = {
    "field_removed": "This setting is not in the form any more.",
    "not_declared": "The preset no longer includes this setting in any formula group.",
    "moved_group": "The preset moved this setting to another formula group, so the saved value may no longer mean the same thing.",
    "type_changed": "This setting is a different kind of control now.",
    "invalid_value": "The saved value does not fit this setting.",
    "option_missing": "The saved value is not one of the available options.",
    "out_of_range": "The saved value is outside the allowed range.",
    "out_of_step": "The saved value does not fit the step of this setting.",
    "pattern_mismatch": "The saved value does not match the format this setting accepts.",
    "options_unavailable": "None of the saved choices are offered any more.",
    "model_unavailable": "The model is not installed or not available to you.",
    "model_wrong_type": "The model is not the kind this setting takes.",
    "model_filtered": "The model is outside what this setting accepts.",
    "lora_unavailable": "The LoRA is not installed or not available to you.",
    "lora_wrong_type": "The LoRA is not the kind this setting takes.",
    "lora_filtered": "The LoRA is outside what this setting accepts.",
    "lora_strength_out_of_range": "The LoRA strength is outside the allowed range.",
    "lora_over_limit": "The list is full, so this LoRA was left out.",
    "variant_changed": "This setting is not in the form variant you are using now.",
    "lora_none_available": "None of the saved LoRAs can be used here.",
}

LORA_BASE_KEYS = ("model", "strength")


@dataclass
class _Skip:
    code: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _Ok:
    value: Any
    skips: List[_Skip] = field(default_factory=list)
    rows: Optional[List[Dict[str, Any]]] = None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _within(value: float, low: Any, high: Any, step: Any) -> Optional[str]:
    if (low is not None and value < low) or (high is not None and value > high):
        return "out_of_range"
    if step:
        offset = (value - (low if low is not None else 0)) / step
        if abs(offset - round(offset)) > 1e-6:
            return "out_of_step"
    return None


def _check_model_ref(
    collaborators: FormulaCollaborators, user: User, value: Any, spec: Dict[str, Any], kind: str, expected_type: Any,
) -> Optional[str]:
    if not is_model_ref(value):
        return f"{kind}_unavailable"
    info = collaborators.models.inspect(model_id_of(value), user)
    if info is None or not info.available:
        return f"{kind}_unavailable"
    if expected_type and info.model_type != expected_type:
        return f"{kind}_wrong_type"
    tags = configuration(spec).get("filter_tags")
    if tags and not set(tags) & set(info.tag_ids):
        return f"{kind}_filtered"
    return None


def _model_kind(collaborators, user, value, spec):
    return _check_model_ref(collaborators, user, value, spec, "model", configuration(spec).get("model_type"))


def _plan_models(collaborators, user, value, spec) -> Any:
    if value in (None, ""):
        return _Ok(value)
    if spec.get("type") == "model":
        code = _model_kind(collaborators, user, value, spec)
        return _Skip(code) if code else _Ok(value)
    if not isinstance(value, list):
        return _Skip("invalid_value")
    kept, skips = [], []
    for item in value:
        code = _model_kind(collaborators, user, item, spec)
        if code:
            skips.append(_Skip(code, {"model": item}))
        else:
            kept.append(item)
    if value and not kept:
        return _Skip("model_unavailable")
    return _Ok(kept, skips)


def _row_extras(spec: Dict[str, Any]) -> List[str]:
    rows = configuration(spec).get("row_fields")
    return [item["name"] for item in rows if isinstance(item, dict) and item.get("name")] if isinstance(rows, list) else []


def _plan_loras(collaborators, user, value, spec, current, lora_mode) -> Any:
    if not isinstance(value, list):
        return _Skip("invalid_value")
    config = configuration(spec)
    expected_type = config.get("model_type", "lora")
    low, high = config.get("strength_min", -2.0), config.get("strength_max", 2.0)
    limit = config.get("max_items", 6)
    allowed = set(LORA_BASE_KEYS) | set(_row_extras(spec))

    skips: List[_Skip] = []
    saved: Dict[str, Dict[str, Any]] = {}
    for row in value:
        if not isinstance(row, dict) or "model" not in row:
            skips.append(_Skip("invalid_value"))
            continue
        code = _check_model_ref(collaborators, user, row["model"], spec, "lora", expected_type)
        strength = row.get("strength", config.get("strength_default", 1.0))
        if code is None and not (_is_number(strength) and low <= strength <= high):
            code = "lora_strength_out_of_range"
        if code:
            skips.append(_Skip(code, {"model": row["model"]}))
            continue
        cleaned = {key: item for key, item in row.items() if key in allowed}
        cleaned["strength"] = strength
        saved[row["model"]] = {**saved.get(row["model"], {}), **cleaned}

    if value and not saved:
        return _Skip("lora_none_available", {"skipped": [item.detail.get("model") for item in skips]})

    current_rows = (
        [row for row in current if isinstance(row, dict) and isinstance(row.get("model"), str)]
        if isinstance(current, list)
        else []
    )
    if lora_mode == "add":
        final = [{**row, **saved.pop(row["model"])} if row["model"] in saved else dict(row) for row in current_rows]
        final.extend(saved.values())
    else:
        final = list(saved.values())
    if limit is not None and len(final) > limit:
        for row in final[limit:]:
            skips.append(_Skip("lora_over_limit", {"model": row["model"]}))
        final = final[:limit]

    old_by_model = {row["model"]: row for row in current_rows}
    final_models = {row["model"] for row in final}
    rows = []
    for row in final:
        before = old_by_model.get(row["model"])
        if before is None:
            status = "added"
        else:
            status = "same" if before == row else "changed"
        rows.append({"model": row["model"], "status": status, "old": before, "new": row})
    for row in current_rows:
        if row["model"] not in final_models:
            rows.append({"model": row["model"], "status": "removed", "old": row, "new": None})
    return _Ok(final, skips, rows)


def _plan_value(collaborators, user, value, spec, current, lora_mode) -> Any:
    field_type = spec.get("type")
    config = configuration(spec)
    if field_type in OPTION_TYPES:
        options = option_values(spec)
        if options is None or value in options or str(value) in [str(item) for item in options]:
            return _Ok(value)
        if value == "" and config.get("allow_empty"):
            return _Ok(value)
        return _Skip("option_missing")
    if field_type in NUMERIC_TYPES:
        if not _is_number(value):
            return _Skip("invalid_value")
        bounds = numeric_bounds(spec)
        code = _within(value, bounds["min"], bounds["max"], bounds["step"])
        return _Skip(code, bounds) if code else _Ok(value)
    if field_type in ("checkbox", "boolean"):
        return _Ok(value) if isinstance(value, bool) else _Skip("invalid_value")
    if field_type in ("textbox", "string"):
        if not isinstance(value, str):
            return _Skip("invalid_value")
        pattern = config.get("pattern") or spec.get("pattern")
        if pattern:
            try:
                if not re.search(pattern, value):
                    return _Skip("pattern_mismatch")
            except re.error:
                return _Ok(value)
        return _Ok(value)
    if field_type in ("checkbox_group", "tags"):
        if not isinstance(value, list):
            return _Skip("invalid_value")
        options = option_values(spec)
        if options is None:
            return _Ok(value)
        kept = [item for item in value if item in options]
        dropped = [item for item in value if item not in options]
        if value and not kept:
            return _Skip("options_unavailable")
        return _Ok(kept, [_Skip("option_missing", {"value": item}) for item in dropped])
    if field_type in MODEL_TYPES:
        return _plan_models(collaborators, user, value, spec)
    if field_type == "lora_picker":
        return _plan_loras(collaborators, user, value, spec, current, lora_mode)
    if field_type == "cloud_options":
        return _Ok(value) if isinstance(value, dict) else _Skip("invalid_value")
    return _Ok(value)


def _skip_entry(name: str, label: str, group_id: Optional[str], skip: _Skip) -> Dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "group_id": group_id,
        "code": skip.code,
        "reason": REASONS.get(skip.code, REASONS["invalid_value"]),
        "detail": skip.detail,
    }


def _live_group_index(loaded: LoadedForm) -> Dict[str, Any]:
    index: Dict[str, Any] = {}
    for group in loaded.groups:
        for name in group.fields:
            index.setdefault(name, group)
    return index


def plan_formula(
    collaborators: FormulaCollaborators, user: User, formula_id: str, request: PlanFormulaRequest
) -> Dict[str, Any]:
    formula = get_formula(collaborators, user.id, formula_id)
    form_name = request.form_name or formula.variant
    try:
        loaded = collaborators.forms.load(formula.preset_id, formula.mode, form_name)
    except FormUnavailable as exc:
        raise FormulaError("form_unavailable", str(exc), 422) from exc

    variant_differs = form_name != formula.variant
    live_groups = _live_group_index(loaded)
    stored_group_of = {name: group["id"] for group in formula.groups for name in group.get("fields", [])}
    current = request.current_values

    changes: List[Dict[str, Any]] = []
    same: List[Dict[str, Any]] = []
    skips: List[Dict[str, Any]] = []
    applied: Dict[str, Dict[str, Any]] = {}

    def record(name: str, label: str, group_id: str, group_label: str, advanced: bool, new: Any,
               companion_of: Optional[str] = None, rows: Optional[list] = None, field_type: Optional[str] = None) -> None:
        old = current.get(name)
        unchanged = name in current and old == new
        if rows is not None:
            unchanged = unchanged or all(row["status"] == "same" for row in rows)
        if unchanged:
            same.append({"name": name, "label": label, "group_id": group_id})
            return
        entry = {
            "name": name, "label": label, "group_id": group_id, "group_label": group_label,
            "type": field_type, "advanced": advanced, "old": old, "new": new,
        }
        if companion_of:
            entry["companion_of"] = companion_of
        if rows is not None:
            entry["rows"] = rows
        changes.append(entry)

    companions: Dict[str, Any] = {}
    for name, value in formula.values.items():
        if name not in stored_group_of:
            companions[name] = value
            continue
        spec = loaded.fields.get(name)
        stored_gid = stored_group_of[name]
        label = (spec or {}).get("title") or name
        live = live_groups.get(name)
        if spec is None:
            code = "variant_changed" if variant_differs else "field_removed"
            skips.append(_skip_entry(name, label, stored_gid, _Skip(code)))
            continue
        if live is None:
            skips.append(_skip_entry(name, label, stored_gid, _Skip("not_declared")))
            continue
        if live.id != stored_gid:
            skips.append(_skip_entry(name, label, stored_gid, _Skip("moved_group", {"now_in": live.id})))
            continue
        stored_type = (formula.signatures.get(name) or {}).get("type")
        if stored_type and stored_type != spec.get("type"):
            skips.append(_skip_entry(name, label, stored_gid, _Skip("type_changed")))
            continue
        outcome = _plan_value(collaborators, user, value, spec, current.get(name), request.lora_mode)
        if isinstance(outcome, _Skip):
            skips.append(_skip_entry(name, label, stored_gid, outcome))
            continue
        for extra in outcome.skips:
            skips.append(_skip_entry(name, label, stored_gid, extra))
        applied[name] = {"live": live, "spec": spec}
        record(
            name, label, live.id, live.label, spec.get("audience") == "advanced", outcome.value,
            rows=outcome.rows, field_type=spec.get("type"),
        )

    for key, value in companions.items():
        owner = owning_field(key, applied)
        if owner is None:
            continue
        info = applied[owner]
        if not companion_accepted(key, owner, info["spec"].get("type")):
            continue
        record(
            key, info["spec"].get("title") or owner, info["live"].id, info["live"].label,
            info["spec"].get("audience") == "advanced", value, companion_of=owner,
            field_type=info["spec"].get("type"),
        )

    return {"changes": changes, "same": same, "skips": skips}
