import json
import logging
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.platform.plugins.organize import SQL_ALIASES, OrganizeFactDefinition, OrganizeItem, OrganizeRegistry

logger = logging.getLogger(__name__)

SUBJECT_ALIASES = SQL_ALIASES


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [v for v in value if v is not None]
    return [value]


def _size_of(value: Any) -> Optional[Tuple[int, int]]:
    if isinstance(value, dict):
        width, height = value.get("width"), value.get("height")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        width, height = value
    else:
        return None
    if width is None or height is None:
        return None
    try:
        return int(width), int(height)
    except (TypeError, ValueError):
        return None


def _numbers(value: Any) -> List[float]:
    found = []
    for entry in as_list(value):
        if isinstance(entry, bool):
            continue
        try:
            number = float(entry)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            found.append(number)
    return found


def _fold(value: Any) -> str:
    return str(value).casefold()


TEXT_TESTS = {
    "contains": lambda text, needle: needle in text,
    "starts_with": lambda text, needle: text.startswith(needle),
    "ends_with": lambda text, needle: text.endswith(needle),
    "is": lambda text, needle: text == needle,
}

NEGATED_TEXT = {"not_contains": "contains", "is_not": "is"}


def _compare_text(operator: str, actual: Any, expected: Any) -> bool:
    needle = _fold(expected or "")
    test = TEXT_TESTS.get(NEGATED_TEXT.get(operator, operator))
    if not needle or test is None:
        return False
    found = any(test(_fold(v), needle) for v in as_list(actual) if isinstance(v, str))
    return not found if operator in NEGATED_TEXT else found


def _strict_numbers(value: Any) -> List[float]:
    return [
        float(v) for v in as_list(value)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    ]


def _compare_interval(operator: str, actual: Any, expected: Any) -> bool:
    values = _strict_numbers(actual)
    targets = _numbers(expected)
    if not values or not targets:
        return False
    low, high, target = min(values), max(values), targets[0]
    if operator == "is":
        return low - 1e-9 <= target <= high + 1e-9
    if operator == "at_least":
        return high >= target
    if operator == "at_most":
        return low <= target
    return False


def _compare_attribute(operator: str, actual: Any, expected: Any) -> bool:
    if not isinstance(expected, Mapping):
        return False
    present = actual.get(expected.get("key")) if isinstance(actual, Mapping) else None
    value_type = expected.get("type")
    target = expected.get("value")
    if value_type == "number":
        return _compare_interval(operator, present, target)
    if value_type == "text":
        return _compare_text(operator, present, target)
    if value_type == "enum":
        return compare("enum", operator, present, target)
    if value_type == "bool":
        return operator == "is" and isinstance(target, bool) and (present is True) == target
    return False


def compare(kind: str, operator: str, actual: Any, expected: Any) -> bool:
    if kind in ("model_ref", "enum"):
        normalize = str if kind == "model_ref" else _fold
        present = {normalize(v) for v in as_list(actual)}
        wanted = [normalize(v) for v in as_list(expected)]
        if operator in ("is", "is_any_of"):
            return any(w in present for w in wanted)
        if operator == "is_not":
            return not any(w in present for w in wanted)
        return False
    if kind == "size":
        target = _size_of(expected)
        if target is None:
            return False
        sizes = [s for s in (_size_of(v) for v in as_list(actual)) if s is not None]
        width, height = target
        if operator == "is":
            return any(w == width and h == height for w, h in sizes)
        if operator == "at_least":
            return any(w >= width and h >= height for w, h in sizes)
        if operator == "at_most":
            return any(w <= width and h <= height for w, h in sizes)
        return False
    if kind == "number":
        targets = _numbers(expected)
        if not targets:
            return False
        target = targets[0]
        values = _numbers(actual)
        if operator == "is":
            return any(abs(v - target) < 1e-9 for v in values)
        if operator == "at_least":
            return any(v >= target for v in values)
        if operator == "at_most":
            return any(v <= target for v in values)
        return False
    if kind == "text":
        return _compare_text(operator, actual, expected)
    if kind == "attribute":
        return _compare_attribute(operator, actual, expected)
    if kind == "tag_list":
        present = {_fold(v) for v in as_list(actual)}
        wanted = [_fold(v) for v in as_list(expected)]
        if not wanted:
            return False
        if operator == "has":
            return all(w in present for w in wanted)
        if operator == "has_not":
            return not any(w in present for w in wanted)
        return False
    if kind == "bool":
        return operator == "is" and bool(actual) == bool(expected)
    return False


def evaluate_condition(fact: OrganizeFactDefinition, condition: Dict[str, Any], item: OrganizeItem) -> bool:
    try:
        actual = fact.extract(item)
    except Exception:
        logger.warning("Auto-organize fact '%s' failed for %s %s", fact.key, item.subject, item.item_id, exc_info=True)
        return False
    return compare(fact.kind, condition.get("operator", ""), actual, condition.get("value"))


def evaluate(registry: OrganizeRegistry, match: str, conditions: Sequence[Dict[str, Any]], item: OrganizeItem) -> bool:
    if not conditions:
        return True
    results = []
    for condition in conditions:
        fact = registry.fact(condition.get("fact", ""))
        if fact is None or item.subject not in fact.subjects:
            return False
        results.append(evaluate_condition(fact, condition, item))
        if match == "any" and results[-1]:
            return True
        if match != "any" and not results[-1]:
            return False
    return all(results) if match != "any" else any(results)


def sql_filter(registry: OrganizeRegistry, subject: str, match: str,
               conditions: Sequence[Dict[str, Any]]) -> Tuple[str, List[Any], bool]:
    alias = SUBJECT_ALIASES[subject]
    clauses: List[str] = []
    params: List[Any] = []
    complete = True
    for condition in conditions:
        fact = registry.fact(condition.get("fact", ""))
        built = None
        if fact is not None and fact.sql is not None:
            try:
                built = fact.sql(condition.get("operator", ""), condition.get("value"), alias)
            except Exception:
                logger.warning("Auto-organize fact '%s' SQL predicate failed", fact.key, exc_info=True)
                built = None
        if built is None:
            complete = False
            continue
        clause, clause_params = built
        clauses.append(f"({clause})")
        params.extend(clause_params)
    if not conditions:
        return "", [], True
    if match == "any":
        if not complete:
            return "", [], False
        return " OR ".join(clauses), params, True
    return " AND ".join(clauses), params, complete


def canonical_conditions(conditions: Iterable[Dict[str, Any]]) -> List[Tuple[str, str, str]]:
    return sorted(
        (c.get("fact", ""), c.get("operator", ""), json.dumps(c.get("value"), sort_keys=True))
        for c in conditions
    )
