import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

MAX_NUMBER_GRID = 200
MAX_STEP_PLACES = 6
MAX_NUMBER_EXPONENT = 12
MAX_NUMBER_DIGITS = 12
MAX_INDEPENDENT_USAGES = 16

_DECIMAL = re.compile(r"^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$")
_USAGE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

Random = Callable[[], float]


@dataclass(frozen=True)
class Decimal:
    coef: int
    exp: int
    digits: int
    negative: bool


@dataclass(frozen=True)
class NumberGrid:
    places: int
    count: int
    minimum: Decimal
    maximum: Decimal
    step: Decimal


def _js_number_text(value: Any) -> str:
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e21:
            return str(int(value))
        return repr(value)
    return str(value)


def parse_decimal(raw: Any) -> Optional[Decimal]:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    if isinstance(raw, float) and not math.isfinite(raw):
        return None
    text = (raw if isinstance(raw, str) else _js_number_text(raw)).strip()
    match = _DECIMAL.match(text)
    if not match:
        return None
    sign, whole, fraction, exponent = match.group(1), match.group(2), match.group(3) or "", match.group(4) or "0"
    if whole == "" and fraction == "":
        return None
    stripped = (whole + fraction).lstrip("0")
    digits_text = stripped or "0"
    exp = int(exponent) - len(fraction)
    digits = len(digits_text)
    if exp + digits - 1 >= MAX_NUMBER_EXPONENT or digits > MAX_NUMBER_DIGITS:
        return None
    coef = int(digits_text) * (-1 if sign == "-" else 1)
    return Decimal(coef, exp, digits, sign == "-")


def _places(value: Decimal) -> int:
    return max(0, -value.exp)


def _at_exponent(value: Decimal, exp: int) -> int:
    return value.coef * 10 ** (value.exp - exp)


def _round_half_up_div(numerator: int, divisor: int) -> int:
    absolute = abs(numerator)
    quotient = (absolute * 2 + divisor) // (divisor * 2)
    return -quotient if numerator < 0 else quotient


def _format_units(units: int, decimals: int, negative: bool) -> str:
    digits = str(abs(units)).rjust(decimals + 1, "0")
    whole = digits[: len(digits) - decimals]
    fraction = "." + digits[len(digits) - decimals:] if decimals > 0 else ""
    return f"{'-' if negative or units < 0 else ''}{whole}{fraction}"


def parse_number_grid(minimum: Any, maximum: Any, step: Any, value: Any = None) -> Optional[NumberGrid]:
    low_value = parse_decimal(minimum)
    high_value = parse_decimal(maximum)
    if low_value is None or high_value is None:
        return None
    step_value = parse_decimal(step)
    if step_value is None or step_value.coef <= 0:
        return None
    exp = min(low_value.exp, high_value.exp, step_value.exp)
    low = _at_exponent(low_value, exp)
    high = _at_exponent(high_value, exp)
    if low > high:
        return None
    step_places = _places(step_value)
    if step_places > MAX_STEP_PLACES or _places(low_value) > step_places:
        return None
    if value is not None and parse_decimal(value) is None:
        return None
    count = (high - low) // _at_exponent(step_value, exp) + 1
    if count > MAX_NUMBER_GRID:
        return None
    return NumberGrid(step_places, count, low_value, high_value, step_value)


def format_grid_index(grid: NumberGrid, index: int) -> str:
    base = _at_exponent(grid.minimum, -grid.places)
    stride = _at_exponent(grid.step, -grid.places)
    return _format_units(base + stride * index, grid.places, False)


def grid_values(grid: NumberGrid) -> List[str]:
    return [format_grid_index(grid, index) for index in range(grid.count)]


def clamp_format(grid: NumberGrid, raw: Any) -> str:
    parsed = grid.minimum if raw is None else parse_decimal(raw)
    value = parsed or grid.minimum
    exp = min(value.exp, grid.minimum.exp, grid.maximum.exp)
    if _at_exponent(value, exp) < _at_exponent(grid.minimum, exp):
        clamped = grid.minimum
    elif _at_exponent(value, exp) > _at_exponent(grid.maximum, exp):
        clamped = grid.maximum
    else:
        clamped = value
    shift = clamped.exp + grid.places
    units = clamped.coef * 10 ** shift if shift >= 0 else _round_half_up_div(clamped.coef, 10 ** -shift)
    return _format_units(units, grid.places, clamped.negative)


def grid_wire(grid: NumberGrid) -> str:
    if grid.count == 1:
        return format_grid_index(grid, 0)
    return "{" + "|".join(grid_values(grid)) + "}"


def _grid_of(definition: Mapping[str, Any]) -> Optional[NumberGrid]:
    return parse_number_grid(definition.get("min"), definition.get("max"), definition.get("step"), definition.get("value"))


def roll_number_value(definition: Mapping[str, Any], random: Random) -> Optional[Tuple[int, str]]:
    grid = _grid_of(definition)
    if grid is None:
        return None
    index = min(math.floor(random() * grid.count), grid.count - 1)
    return index, format_grid_index(grid, index)


def build_number_wire_value(definition: Mapping[str, Any]) -> str:
    grid = _grid_of(definition)
    if grid is None:
        return ""
    if definition.get("mode") == "pin":
        return clamp_format(grid, definition.get("value"))
    return grid_wire(grid)


def option_text(option: Any) -> str:
    if isinstance(option, str):
        return option
    if isinstance(option, Mapping):
        text = option.get("text")
        return text if isinstance(text, str) else ""
    return ""


def option_when(option: Any) -> Optional[Mapping[str, Any]]:
    if isinstance(option, Mapping):
        when = option.get("when")
        return when if isinstance(when, Mapping) else None
    return None


def normalize_variable_def(stored: Any) -> Dict[str, Any]:
    if stored is None:
        return {"type": "text", "value": ""}
    if isinstance(stored, str):
        return {"type": "text", "value": stored}
    if not isinstance(stored, Mapping):
        return {"type": "text", "value": ""}
    kind = stored.get("type")
    if kind in ("text", "number"):
        return dict(stored)
    options = stored.get("options")
    pinned = stored.get("pinnedIndex")
    mode = stored.get("mode") or ("pin" if pinned is not None else "shuffle")
    definition: Dict[str, Any] = {
        "type": "choice",
        "options": list(options) if isinstance(options, list) else [],
        "mode": mode,
        "pinnedIndex": pinned,
    }
    if stored.get("linked"):
        definition["linked"] = True
    return definition


def _dependencies(name: str, variables: Mapping[str, Any]) -> List[str]:
    definition = normalize_variable_def(variables.get(name))
    found: List[str] = []
    if definition["type"] == "choice":
        for option in definition["options"]:
            when = option_when(option)
            if when and when.get("var") != name and variables.get(when.get("var")) is not None:
                if when["var"] not in found:
                    found.append(when["var"])
    return found


def _dependents(name: str, variables: Mapping[str, Any]) -> List[str]:
    return [other for other in variables if other != name and name in _dependencies(other, variables)]


def resolve_variable_order(variables: Optional[Mapping[str, Any]]) -> List[str]:
    if not variables:
        return []
    order: List[str] = []
    done: Set[str] = set()
    active: Set[str] = set()

    def visit(name: str) -> None:
        if name in done or name in active:
            return
        active.add(name)
        for dependency in _dependencies(name, variables):
            visit(dependency)
        active.discard(name)
        done.add(name)
        order.append(name)

    for name in variables:
        visit(name)
    return order


def _eligible(option: Any, resolved: Mapping[str, str]) -> bool:
    when = option_when(option)
    if not when:
        return True
    actual = resolved.get(when.get("var"))
    return actual is not None and actual in (when.get("values") or [])


def is_variable_linked(name: str, variables: Mapping[str, Any], readers: Set[str]) -> bool:
    definition = normalize_variable_def(variables.get(name))
    if definition["type"] == "text" or definition.get("mode") != "shuffle":
        return True
    return definition.get("linked") is True or bool(_dependents(name, variables)) or name in readers


def usage_roll_key(name: str, ordinal: int) -> str:
    capped = min(max(int(ordinal), 1), MAX_INDEPENDENT_USAGES)
    return name if capped == 1 else f"{name}-{capped}"


def _usage_names(text: str) -> List[Tuple[str, int, int]]:
    return [(m.group(1), m.start(), m.end()) for m in _USAGE.finditer(text)]


def apply_independent_usages(
    prompts: List[Dict[str, str]], variables: Optional[Mapping[str, Any]], readers: Set[str]
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    usage_counts: Dict[str, int] = {}
    if variables is None:
        return prompts, usage_counts
    seen: Dict[str, int] = {}
    rewritten: List[Dict[str, str]] = []
    for prompt in prompts:
        texts = [prompt["positive"], prompt["negative"]]
        numbered: List[List[Dict[str, Any]]] = []
        for text in texts:
            usages = []
            for name, _, _ in _usage_names(text):
                seen[name] = seen.get(name, 0) + 1
                usages.append({"name": name, "ordinal": seen[name], "total": 0})
            numbered.append(usages)
        for usages in numbered:
            for usage in usages:
                usage["total"] = seen[usage["name"]]
        out: List[str] = []
        for text, usages in zip(texts, numbered):
            result = ""
            cursor = 0
            for usage, (_, start, end) in zip(usages, _usage_names(text)):
                if is_variable_linked(usage["name"], variables, readers):
                    continue
                usage_counts[usage["name"]] = max(usage_counts.get(usage["name"], 1), usage["total"])
                result += text[cursor:start] + "${" + usage_roll_key(usage["name"], usage["ordinal"]) + "}"
                cursor = end
            out.append(result + text[cursor:])
        rewritten.append({**prompt, "positive": out[0], "negative": out[1]})
    return rewritten, usage_counts


def _valid_option_texts(definition: Mapping[str, Any]) -> List[str]:
    return [text for text in (option_text(o).strip() for o in definition["options"]) if text]


def _variable_wire_value(definition: Mapping[str, Any]) -> str:
    if definition["type"] == "text":
        return definition.get("value") or ""
    if definition["type"] == "number":
        return build_number_wire_value(definition)
    valid = _valid_option_texts(definition)
    if not valid:
        return ""
    pinned = definition.get("pinnedIndex")
    if definition.get("mode") == "pin" and pinned is not None:
        options = definition["options"]
        text = option_text(options[pinned]).strip() if isinstance(pinned, int) and 0 <= pinned < len(options) else ""
        if text:
            return text
    if len(valid) == 1:
        return valid[0]
    return "{" + "|".join(valid) + "}"


def _usages_for(name: str, variables: Mapping[str, Any], readers: Set[str], usage_counts: Mapping[str, int]) -> int:
    if is_variable_linked(name, variables, readers):
        return 1
    return min(max(usage_counts.get(name, 1), 1), MAX_INDEPENDENT_USAGES)


def build_variables_for_submit(
    variables: Optional[Mapping[str, Any]],
    random: Random,
    now: Callable[[], float],
    usage_counts: Optional[Mapping[str, int]] = None,
    readers: Optional[Set[str]] = None,
) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    wire: Dict[str, str] = {}
    rolls: Dict[str, Dict[str, Any]] = {}
    if variables is None:
        return wire, rolls
    counts = usage_counts or {}
    read = readers or set()
    resolved: Dict[str, str] = {}

    for name in resolve_variable_order(variables):
        stored = variables.get(name)
        if stored is None:
            continue
        definition = normalize_variable_def(stored)

        if definition["type"] == "text":
            value = definition.get("value") or ""
            if value:
                wire[name] = value
            resolved[name] = value.strip()
            continue

        if definition["type"] == "number":
            if definition.get("mode") == "shuffle":
                for ordinal in range(1, _usages_for(name, variables, read, counts) + 1):
                    pick = roll_number_value(definition, random)
                    if pick is None:
                        break
                    key = usage_roll_key(name, ordinal)
                    wire[key] = pick[1]
                    rolls[key] = {"optionIndex": pick[0], "value": pick[1], "rolledAt": now()}
                continue
            number = build_number_wire_value(definition)
            if number:
                wire[name] = number
            continue

        if definition.get("mode") == "shuffle":
            valid = [
                {"option": o, "index": i, "text": option_text(o).strip()}
                for i, o in enumerate(definition["options"])
                if option_text(o).strip()
            ]
            if not valid:
                continue
            has_conditions = any(option_when(entry["option"]) for entry in valid)
            eligible = [entry for entry in valid if _eligible(entry["option"], resolved)]
            if not eligible:
                if has_conditions:
                    rolls[name] = {"optionIndex": -1, "value": "", "rolledAt": now(), "eligible": 0, "total": len(valid)}
                continue
            for ordinal in range(1, _usages_for(name, variables, read, counts) + 1):
                pick = eligible[math.floor(random() * len(eligible))]
                key = usage_roll_key(name, ordinal)
                wire[key] = pick["text"]
                if ordinal == 1:
                    resolved[name] = pick["text"]
                roll: Dict[str, Any] = {"optionIndex": pick["index"], "value": pick["text"], "rolledAt": now()}
                if has_conditions:
                    roll["eligible"] = len(eligible)
                    roll["total"] = len(valid)
                    when = option_when(pick["option"])
                    if when:
                        roll["because"] = {"var": when.get("var"), "value": resolved.get(when.get("var"), "")}
                rolls[key] = roll
            continue

        value = _variable_wire_value(definition)
        if value:
            wire[name] = value
        if definition.get("mode") == "pin":
            resolved[name] = value

    return wire, rolls
