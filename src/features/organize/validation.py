import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from src.platform.plugins.organize import ATTRIBUTE_VALUE_OPERATORS, SUBJECTS, OrganizeFactDefinition, OrganizeRegistry

MAX_CONDITIONS = 20
MAX_ACTIONS = 10
MAX_LIST_VALUES = 50
MAX_TEXT = 200
MAX_NAME = 120
MAX_TAG_NAME = 100
MAX_SIDE = 32768

Problem = Dict[str, str]


def problem(path: str, code: str, message: str) -> Problem:
    return {"path": path, "code": code, "message": message}


def _clean_str(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        return None
    return cleaned


def _clean_str_list(value: Any, limit: int, max_items: int) -> Optional[List[str]]:
    if not isinstance(value, list) or not value or len(value) > max_items:
        return None
    cleaned = [_clean_str(v, limit) for v in value]
    if any(v is None for v in cleaned):
        return None
    return list(dict.fromkeys(cleaned))


def normalize_value(kind: str, operator: str, value: Any, allowed: Optional[List[str]]) -> Tuple[Any, Optional[str]]:
    if kind in ("model_ref", "enum"):
        if operator == "is_any_of":
            values = _clean_str_list(value, MAX_TEXT, MAX_LIST_VALUES)
            if values is None:
                return None, "Pick at least one value"
        else:
            single = _clean_str(value, MAX_TEXT)
            if single is None:
                return None, "Pick a value"
            values = [single]
        if allowed is not None and kind == "enum":
            allowed_folded = {a.casefold() for a in allowed}
            if any(v.casefold() not in allowed_folded for v in values):
                return None, "That value is not one of the choices"
        return (values if operator == "is_any_of" else values[0]), None
    if kind == "size":
        if not isinstance(value, dict):
            return None, "Pick a width and a height"
        try:
            width, height = int(value.get("width")), int(value.get("height"))
        except (TypeError, ValueError):
            return None, "Pick a width and a height"
        if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
            return None, "Width and height must be between 1 and 32768"
        return {"width": width, "height": height}, None
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None, "Enter a number"
        return value, None
    if kind == "text":
        text = _clean_str(value, MAX_TEXT)
        if text is None:
            return None, "Enter some text, up to 200 characters"
        return text, None
    if kind == "tag_list":
        tags = _clean_str_list(value, MAX_TAG_NAME, 20)
        if tags is None:
            return None, "Pick between 1 and 20 tags"
        return tags, None
    if kind == "bool":
        if not isinstance(value, bool):
            return None, "Choose yes or no"
        return value, None
    return None, "Unknown field kind"


AttributeSpecs = Callable[[OrganizeFactDefinition, str], Mapping[str, Mapping[str, Any]]]


def normalize_attribute(operator: str, value: Any,
                        specs: Mapping[str, Mapping[str, Any]]) -> Tuple[Any, Optional[Tuple[str, str, str]]]:
    if not isinstance(value, dict) or not isinstance(value.get("key"), str) or not value["key"]:
        return None, ("value", "bad_value", "Pick an attribute")
    spec = specs.get(value["key"])
    if spec is None:
        return None, ("value", "bad_value", "That attribute is not available")
    value_type = spec.get("type")
    label = str(spec.get("label") or value["key"])
    if operator not in ATTRIBUTE_VALUE_OPERATORS.get(value_type, ()):
        return None, ("operator", "unknown_operator", f"{label} cannot be compared that way")
    allowed = None
    if value_type == "enum" and spec.get("choices"):
        allowed = [str(c.get("value")) for c in spec["choices"]]
    inner, error = normalize_value(value_type, operator, value.get("value"), allowed)
    if error:
        return None, ("value", "bad_value", error)
    return {"key": value["key"], "type": value_type, "label": label, "value": inner}, None


def validate_conditions(registry: OrganizeRegistry, subject: str, conditions: Any,
                        attribute_specs: Optional[AttributeSpecs] = None) -> Tuple[List[Dict[str, Any]], List[Problem]]:
    problems: List[Problem] = []
    if not isinstance(conditions, list):
        return [], [problem("conditions", "bad_value", "Conditions must be a list")]
    if len(conditions) > MAX_CONDITIONS:
        problems.append(problem("conditions", "too_many_conditions", f"A rule can have at most {MAX_CONDITIONS} conditions"))
    cleaned: List[Dict[str, Any]] = []
    for index, condition in enumerate(conditions[:MAX_CONDITIONS]):
        path = f"conditions.{index}"
        if not isinstance(condition, dict):
            problems.append(problem(path, "bad_value", "Each condition must be an object"))
            continue
        fact = registry.fact(condition.get("fact") or "")
        if fact is None:
            problems.append(problem(f"{path}.fact", "unknown_fact", "This field is not available"))
            continue
        if subject not in fact.subjects:
            problems.append(problem(f"{path}.fact", "fact_subject", f"{fact.label} is not available here"))
            continue
        operator = condition.get("operator")
        if operator not in fact.allowed_operators():
            problems.append(problem(f"{path}.operator", "unknown_operator", f"{fact.label} cannot be compared that way"))
            continue
        if fact.kind == "attribute":
            specs = attribute_specs(fact, subject) if attribute_specs is not None else {}
            value, failure = normalize_attribute(operator, condition.get("value"), specs)
            if failure:
                where, code, message = failure
                problems.append(problem(f"{path}.{where}", code, message))
                continue
            cleaned.append({"fact": fact.key, "operator": operator, "value": value})
            continue
        allowed = None
        if fact.options and fact.options_handler is None:
            allowed = [str(o.get("value")) for o in fact.options]
        value, error = normalize_value(fact.kind, operator, condition.get("value"), allowed)
        if error:
            problems.append(problem(f"{path}.value", "bad_value", error))
            continue
        cleaned.append({"fact": fact.key, "operator": operator, "value": value})
    return cleaned, problems


def _validate_config(schema, config: Dict[str, Any], path: str) -> Tuple[Dict[str, Any], List[Problem]]:
    problems: List[Problem] = []
    cleaned: Dict[str, Any] = {}
    for field_def in schema:
        key = field_def.get("key")
        kind = field_def.get("kind")
        raw = config.get(key, field_def.get("default"))
        if raw is None or raw == "" or raw == []:
            if field_def.get("required"):
                problems.append(problem(f"{path}.{key}", "bad_config", f"{field_def.get('label', key)} is required"))
            cleaned[key] = field_def.get("default") if raw is None else raw
            continue
        if kind in ("collection", "text"):
            value = _clean_str(raw, MAX_TEXT)
        elif kind == "tag_list":
            value = _clean_str_list(raw, MAX_TAG_NAME, 20)
        elif kind == "bool":
            value = raw if isinstance(raw, bool) else None
        elif kind == "number":
            value = raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
        elif kind == "enum":
            options = [str(o.get("value")) for o in field_def.get("options") or []]
            value = raw if isinstance(raw, str) and (not options or raw in options) else None
        else:
            value = raw
        if value is None:
            problems.append(problem(f"{path}.{key}", "bad_config", f"{field_def.get('label', key)} is not valid"))
            continue
        cleaned[key] = value
    return cleaned, problems


def validate_actions(registry: OrganizeRegistry, subject: str, actions: Any,
                     is_admin: bool) -> Tuple[List[Dict[str, Any]], List[Problem]]:
    problems: List[Problem] = []
    if not isinstance(actions, list) or not actions:
        return [], [problem("actions", "no_actions", "Add at least one thing for the rule to do")]
    if len(actions) > MAX_ACTIONS:
        problems.append(problem("actions", "too_many_actions", f"A rule can have at most {MAX_ACTIONS} actions"))
    cleaned: List[Dict[str, Any]] = []
    for index, action in enumerate(actions[:MAX_ACTIONS]):
        path = f"actions.{index}"
        if not isinstance(action, dict):
            problems.append(problem(path, "bad_config", "Each action must be an object"))
            continue
        definition = registry.action(action.get("action") or "")
        if definition is None:
            problems.append(problem(f"{path}.action", "unknown_action", "This action is not available"))
            continue
        if subject not in definition.subjects:
            problems.append(problem(f"{path}.action", "action_subject", f"{definition.label} is not available here"))
            continue
        if definition.requires_admin and not is_admin:
            problems.append(problem(f"{path}.action", "action_admin_only", f"Only an admin can use {definition.label}"))
            continue
        config = action.get("config") or {}
        if not isinstance(config, dict):
            problems.append(problem(f"{path}.config", "bad_config", "Settings must be an object"))
            continue
        cleaned_config, config_problems = _validate_config(definition.config_schema, config, f"{path}.config")
        problems.extend(config_problems)
        if definition.key == "add_to_collection" and not cleaned_config.get("collection_id") and not cleaned_config.get("collection_name"):
            problems.append(problem(f"{path}.config.collection_id", "bad_config", "Pick a collection or name a new one"))
        cleaned.append({"action": definition.key, "config": cleaned_config})
    return cleaned, problems


def validate_rule_shape(registry: OrganizeRegistry, subject: Any, match: Any, conditions: Any, actions: Any,
                        is_admin: bool, require_actions: bool = True,
                        attribute_specs: Optional[AttributeSpecs] = None) -> Tuple[Dict[str, Any], List[Problem]]:
    problems: List[Problem] = []
    if subject not in SUBJECTS:
        return {}, [problem("subject", "bad_value", "Pick generations, library uploads or models")]
    if match not in ("all", "any"):
        problems.append(problem("match", "bad_value", "Match must be all or any"))
    cleaned_conditions, condition_problems = validate_conditions(
        registry, subject, conditions if conditions is not None else [], attribute_specs
    )
    problems.extend(condition_problems)
    cleaned_actions: List[Dict[str, Any]] = []
    if require_actions or actions:
        cleaned_actions, action_problems = validate_actions(registry, subject, actions, is_admin)
        problems.extend(action_problems)
    return {"subject": subject, "match": match, "conditions": cleaned_conditions, "actions": cleaned_actions}, problems


def validate_name(name: Any) -> Tuple[Optional[str], List[Problem]]:
    cleaned = _clean_str(name, MAX_NAME)
    if cleaned is None:
        return None, [problem("name", "name_required", "Give the rule a name, up to 120 characters")]
    return cleaned, []
