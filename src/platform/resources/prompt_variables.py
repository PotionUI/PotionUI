"""Plain-language rendering of the generate tab's typed prompt variables.

The chat form-state snapshot carries a compact ``variables`` list alongside
``form_data`` (see the frontend ``buildVariablesSnapshot`` helper). A prompt
variable is a named ``${...}`` placeholder the user can reuse across their
prompt; it is either free ``text`` or a ``choice`` of options resolved per a
mode (shuffle once per generation / pin one option / re-roll per image).

Both the ``@form`` resource dump and the ``get_form_state`` tool render this
list into short human sentences the chat model reads — e.g.::

    mood: one of noir, sunlit — shuffles each generation; last roll: sunlit

This module is the single pure renderer shared by both surfaces. It is
defensive about the untrusted client shape and applies the same
count/length caps FormResourceProvider uses so a padded snapshot can never
bloat the prompt.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

# Caps mirror FormResourceProvider's discipline (small-model payload budget).
_MAX_VARIABLES = 24
_MAX_OPTIONS = 12
_MAX_VALUE_CHARS = 80
_MAX_NAME_CHARS = 60

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VALID_MODES = {"shuffle", "pin", "per-image"}
MAX_NAME_CHARS = _MAX_NAME_CHARS
MAX_VARIABLES = _MAX_VARIABLES


def _clip(text: str, limit: int) -> str:
    return text[:limit] + "…" if len(text) > limit else text


def _normalize_when(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    var = raw.get("var")
    values = raw.get("values")
    if not isinstance(var, str) or not var.strip():
        return None
    if not isinstance(values, (list, tuple)):
        return None
    out_values = [
        str(v).strip() for v in values if isinstance(v, (str, int, float)) and str(v).strip()
    ]
    return {"var": var.strip(), "values": out_values}


def normalize_option(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, (str, int, float)):
        text = str(raw).strip()
        return {"text": text, "when": None} if text else None
    if isinstance(raw, dict):
        text = raw.get("text")
        if not isinstance(text, (str, int, float)):
            return None
        text = str(text).strip()
        if not text:
            return None
        return {"text": text, "when": _normalize_when(raw.get("when"))}
    return None


def valid_options(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[Dict[str, Any]] = []
    for opt in raw:
        normalized = normalize_option(opt)
        if normalized is not None:
            out.append(normalized)
    return out


def option_texts(options: List[Dict[str, Any]]) -> List[str]:
    return [o["text"] for o in options]


def variable_dependencies(var: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(var, dict) or var.get("type") != "choice":
        return []
    deps: List[str] = []
    for opt in valid_options(var.get("options")):
        when = opt.get("when")
        if when and when["var"] not in deps:
            deps.append(when["var"])
    return deps


def is_downstream(start: str, target: str, variables_by_name: Dict[str, Any]) -> bool:
    seen = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(variable_dependencies(variables_by_name.get(current)))
    return False


def format_option(option: Dict[str, Any]) -> str:
    when = option.get("when")
    if not when:
        return option["text"]
    return f"{option['text']} (when ${when['var']} = {', '.join(when['values'])})"


def dependency_prefix(deps: List[str]) -> str:
    if not deps:
        return ""
    return f"resolves after {', '.join('$' + d for d in deps)} — "


def _mode_phrase(mode: Any, options: List[str], pinned_index: Any) -> str:
    if mode == "pin":
        if isinstance(pinned_index, int) and 0 <= pinned_index < len(options):
            return f"pinned to {options[pinned_index]}"
        return "pinned"
    if mode == "per-image":
        return "re-rolls independently per image"
    # 'shuffle' is the default (and the value for any unknown/missing mode).
    return "shuffles each generation"


def _render_choice(name: str, var: dict) -> Optional[str]:
    options = valid_options(var.get("options"))
    if not options:
        return None
    shown = options[:_MAX_OPTIONS]
    listing = ", ".join(_clip(format_option(o), _MAX_VALUE_CHARS) for o in shown)
    if len(options) > _MAX_OPTIONS:
        listing += ", …"
    deps = variable_dependencies(var)
    prefix = f"{name} — {dependency_prefix(deps)}" if deps else f"{name}: "
    texts = option_texts(options)
    line = f"{prefix}one of {listing} — {_mode_phrase(var.get('mode'), texts, var.get('pinnedIndex'))}"
    roll = var.get("lastRoll")
    if isinstance(roll, (str, int, float)) and str(roll).strip():
        line += f"; last roll: {_clip(str(roll).strip(), _MAX_VALUE_CHARS)}"
    return line


def _render_text(name: str, var: dict) -> str:
    value = var.get("value")
    if not isinstance(value, (str, int, float)) or str(value).strip() == "":
        return f"{name}: free text (empty)"
    return f"{name}: {_clip(str(value).strip(), _MAX_VALUE_CHARS)}"


def render_prompt_variable_lines(variables: Any) -> List[str]:
    """One plain-language sentence per prompt variable, capped and defensive.

    Returns an empty list when ``variables`` is absent, malformed, or contains
    no renderable entries — callers treat that as "no prompt variables".
    """
    if not isinstance(variables, (list, tuple)):
        return []
    lines: List[str] = []
    for var in variables[:_MAX_VARIABLES]:
        if not isinstance(var, dict):
            continue
        raw_name = var.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        name = _clip(raw_name.strip(), _MAX_NAME_CHARS)
        if var.get("type") == "choice":
            line = _render_choice(name, var)
            if line is None:
                continue
        else:
            line = _render_text(name, var)
        lines.append(line)
    return lines


def name_error(name: str) -> Optional[str]:
    if not NAME_RE.match(name) or len(name) > MAX_NAME_CHARS:
        return (
            f"'{name}' is not a valid variable name. Use letters, digits, and "
            f"underscores, starting with a letter or underscore, up to {MAX_NAME_CHARS} characters."
        )
    return None


def validate_condition(
    name: str,
    when: Dict[str, Any],
    variables_by_name: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    var = when["var"]
    values = when["values"]

    if not values:
        return None, f"'{name}': when.values must not be empty."

    if var == name:
        return None, f"'{name}': when.var cannot reference '{name}' itself."

    ref = variables_by_name.get(var)
    if not isinstance(ref, dict) or ref.get("type") != "choice":
        choices = [
            n for n, v in variables_by_name.items() if isinstance(v, dict) and v.get("type") == "choice"
        ]
        listing = ", ".join(choices) if choices else "none defined yet"
        return None, (
            f"'{name}': when.var '{var}' is not a choice variable on this tab; "
            f"choice variables: {listing}."
        )

    if is_downstream(var, name, variables_by_name):
        return None, (
            f"'{name}': when.var '{var}' would make a cycle ({var} already resolves after {name})."
        )

    ref_texts = option_texts(valid_options(ref.get("options")))
    bad = [v for v in values if v not in ref_texts]
    if bad:
        return None, (
            f"'{name}': when.values {json.dumps(bad)} are not options of ${var}; "
            f"its options are {', '.join(ref_texts)}."
        )

    return {"var": var, "values": values}, None


def validate_variables_map(variables: Any) -> List[str]:
    errors: List[str] = []
    if variables is None:
        return errors
    if not isinstance(variables, dict):
        return ["variables must be an object mapping name to definition."]

    if len(variables) > MAX_VARIABLES:
        errors.append(f"Too many prompt variables: {len(variables)} (max {MAX_VARIABLES}).")

    for raw_name, definition in variables.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            errors.append("Empty variable name.")
            continue
        name = raw_name.strip()
        err = name_error(name)
        if err:
            errors.append(err)
            continue

        if not isinstance(definition, dict):
            errors.append(f"'{name}': definition must be an object.")
            continue

        var_type = definition.get("type")
        if var_type == "text":
            value = definition.get("value")
            if value is not None and not isinstance(value, str):
                errors.append(f"'{name}': a text variable's value must be a string.")
            continue

        if var_type != "choice":
            errors.append(f"'{name}': invalid type '{var_type}'. Use text or choice.")
            continue

        raw_options = definition.get("options")
        if not isinstance(raw_options, (list, tuple)) or not raw_options:
            errors.append(f"'{name}': a choice variable needs at least one non-empty option.")
            continue
        texts: List[str] = []
        condition_failed = False
        for raw_opt in raw_options:
            normalized = normalize_option(raw_opt)
            if normalized is None:
                continue
            texts.append(normalized["text"])
            when = normalized["when"]
            if when is not None:
                _, condition_error = validate_condition(name, when, variables)
                if condition_error:
                    errors.append(condition_error)
                    condition_failed = True

        if not texts:
            errors.append(f"'{name}': a choice variable needs at least one non-empty option.")
            continue
        if condition_failed:
            continue

        mode = definition.get("mode") or "shuffle"
        if mode not in VALID_MODES:
            errors.append(f"'{name}': invalid mode '{mode}'. Use shuffle, pin, or per-image.")
            continue

        pinned_index = definition.get("pinnedIndex")
        if pinned_index is not None and (
            not isinstance(pinned_index, int) or not (0 <= pinned_index < len(texts))
        ):
            errors.append(f"'{name}': pinnedIndex must be a valid index into options.")

    return errors
