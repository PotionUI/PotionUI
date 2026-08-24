"""
Safe expression evaluation for automation conditions.

Two building blocks, neither of which ever calls `eval()`:
- `get_path`: a dot-path getter over dicts/lists/objects (supports list
  indices, e.g. "parts.1" or "event.tags[0]").
- `OPERATORS`: a fixed table of comparison operators used by
  `condition.compare` / `condition.path_match`.

`compile_expression` wraps `jinja2.sandbox.SandboxedEnvironment` for
`condition.jinja_expression` and Jinja-templated action config values. The
sandbox blocks attribute/method escapes (e.g. `__class__`) but not expensive
computation - acceptable for the MVP (see plan risk #4).
"""

import re
from typing import Any, Callable, Dict, Optional

from jinja2.sandbox import SandboxedEnvironment
from jinja2 import TemplateError

_INDEX_RE = re.compile(r"^(.*)\[(\d+)\]$")


class ExpressionError(ValueError):
    """Raised when a dot-path or expression cannot be resolved/compiled."""


def get_path(data: Any, path: str, default: Any = None) -> Any:
    """
    Resolve a dot-separated path against `data` (dicts, lists, and objects).

    Supports plain numeric segments as list indices ("parts.1") and the
    trailing bracket form ("tags[0]"). Missing keys/indices/attributes
    resolve to `default` rather than raising.
    """
    if not path:
        return default

    current = data
    for raw_segment in path.split("."):
        if current is None:
            return default

        match = _INDEX_RE.match(raw_segment)
        segments = [match.group(1), match.group(2)] if match else [raw_segment]

        for segment in segments:
            if segment == "":
                continue
            if isinstance(current, dict):
                if segment in current:
                    current = current[segment]
                else:
                    return default
            elif isinstance(current, (list, tuple)):
                try:
                    idx = int(segment)
                except ValueError:
                    return default
                if -len(current) <= idx < len(current):
                    current = current[idx]
                else:
                    return default
            elif segment.isdigit() and hasattr(current, "__getitem__"):
                try:
                    current = current[int(segment)]
                except (IndexError, KeyError, TypeError):
                    return default
            elif hasattr(current, segment):
                current = getattr(current, segment)
            else:
                return default

    return current


def _op_equals(a: Any, b: Any) -> bool:
    return a == b


def _op_not_equals(a: Any, b: Any) -> bool:
    return a != b


def _op_contains(a: Any, b: Any) -> bool:
    if a is None:
        return False
    try:
        return b in a
    except TypeError:
        return False


def _op_not_contains(a: Any, b: Any) -> bool:
    return not _op_contains(a, b)


def _op_gt(a: Any, b: Any) -> bool:
    try:
        return float(a) > float(b)
    except (TypeError, ValueError):
        return False


def _op_gte(a: Any, b: Any) -> bool:
    try:
        return float(a) >= float(b)
    except (TypeError, ValueError):
        return False


def _op_lt(a: Any, b: Any) -> bool:
    try:
        return float(a) < float(b)
    except (TypeError, ValueError):
        return False


def _op_lte(a: Any, b: Any) -> bool:
    try:
        return float(a) <= float(b)
    except (TypeError, ValueError):
        return False


def _op_starts_with(a: Any, b: Any) -> bool:
    return isinstance(a, str) and isinstance(b, str) and a.startswith(b)


def _op_ends_with(a: Any, b: Any) -> bool:
    return isinstance(a, str) and isinstance(b, str) and a.endswith(b)


# Length-capped to avoid pathological regex/input causing runaway matching.
_REGEX_MAX_LEN = 4096


def _op_regex(a: Any, b: Any) -> bool:
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    if len(a) > _REGEX_MAX_LEN or len(b) > _REGEX_MAX_LEN:
        return False
    try:
        return re.search(b, a) is not None
    except re.error:
        return False


def _op_is_empty(a: Any, _b: Any) -> bool:
    if a is None:
        return True
    if isinstance(a, (str, list, tuple, dict, set)):
        return len(a) == 0
    return False


def _op_is_not_empty(a: Any, b: Any) -> bool:
    return not _op_is_empty(a, b)


# Fixed operator table for `condition.compare` / `condition.path_match`.
# Each operator is a pure `(actual, expected) -> bool` callable - no eval().
OPERATORS: Dict[str, Callable[[Any, Any], bool]] = {
    "equals": _op_equals,
    "not_equals": _op_not_equals,
    "contains": _op_contains,
    "not_contains": _op_not_contains,
    "gt": _op_gt,
    "gte": _op_gte,
    "lt": _op_lt,
    "lte": _op_lte,
    "starts_with": _op_starts_with,
    "ends_with": _op_ends_with,
    "regex": _op_regex,
    "is_empty": _op_is_empty,
    "is_not_empty": _op_is_not_empty,
}


def apply_operator(operator: str, actual: Any, expected: Any = None) -> bool:
    """Look up and apply an operator by name. Raises ExpressionError for unknown operators."""
    op = OPERATORS.get(operator)
    if op is None:
        raise ExpressionError(f"Unknown operator: '{operator}'")
    return op(actual, expected)


# Single shared sandboxed environment - no filesystem/network loaders, so
# there's nothing beyond expression evaluation for it to reach.
_sandbox_env = SandboxedEnvironment()


def compile_expression(expression: str):
    """Compile a Jinja2 boolean/value expression under the sandbox. Raises ExpressionError."""
    try:
        return _sandbox_env.compile_expression(expression)
    except TemplateError as exc:
        raise ExpressionError(f"Invalid expression '{expression}': {exc}") from exc


def eval_expression(expression: str, context: Optional[Dict[str, Any]] = None) -> Any:
    """Compile and evaluate a Jinja2 expression against `context` (typically {event, upstream})."""
    compiled = compile_expression(expression)
    return compiled(**(context or {}))


def render_template(template_str: str, context: Optional[Dict[str, Any]] = None) -> str:
    """Render a Jinja2 template string (action config values) under the sandbox."""
    try:
        template = _sandbox_env.from_string(template_str)
        return template.render(**(context or {}))
    except TemplateError as exc:
        raise ExpressionError(f"Invalid template '{template_str}': {exc}") from exc
