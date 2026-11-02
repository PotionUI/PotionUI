from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

MAX_SOURCE_CHARS = 2000
MAX_STRING_CHARS = 64
MAX_EXPONENT = 1024.0


class DeriveError(ValueError):
    pass


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise DeriveError("the expression does not give a finite number")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    raise DeriveError(f"a {type(value).__name__} is not a number")


def _floor(value: Any) -> int:
    return math.floor(_finite(float(_number(value))))


def _round(value: Any) -> int:
    return math.floor(_finite(float(_number(value))) + 0.5)


def _sqrt(value: Any) -> float:
    number = float(_number(value))
    if number < 0:
        raise DeriveError("sqrt of a negative number")
    return math.sqrt(number)


def _pow(base: Any, exponent: Any) -> float:
    left, right = float(_number(base)), float(_number(exponent))
    if abs(right) > MAX_EXPONENT:
        raise DeriveError("the exponent is too large")
    if left == 0 and right < 0:
        raise DeriveError("zero cannot be raised to a negative power")
    try:
        result = math.pow(left, right)
    except (OverflowError, ValueError):
        raise DeriveError("the power is out of range") from None
    return _finite(result)


def _clamp(value: Any, low: Any, high: Any) -> Any:
    return max(_number(low), min(_number(high), _number(value)))


def _extremum(pick: Callable) -> Callable:
    def apply(*values: Any) -> Any:
        if not values:
            raise DeriveError("min and max need at least one value")
        return pick(_number(value) for value in values)

    return apply


FUNCTIONS: dict[str, tuple[Callable, int, int]] = {
    "min": (_extremum(min), 1, 16),
    "max": (_extremum(max), 1, 16),
    "clamp": (_clamp, 3, 3),
    "abs": (lambda value: abs(_number(value)), 1, 1),
    "floor": (_floor, 1, 1),
    "round": (_round, 1, 1),
    "sqrt": (_sqrt, 1, 1),
    "sin": (lambda value: math.sin(_number(value)), 1, 1),
    "cos": (lambda value: math.cos(_number(value)), 1, 1),
    "pow": (_pow, 2, 2),
}
CONSTANTS = {"pi": math.pi}
RESERVED = frozenset({"media", "time", "pi", *FUNCTIONS})

_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.IfExp, ast.Call, ast.Name,
    ast.Attribute, ast.Constant, ast.Load, ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, ast.Add, ast.Sub,
    ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def _divide(left: Any, right: Any) -> float:
    if _number(right) == 0:
        raise DeriveError("division by zero")
    return _number(left) / _number(right)


def _modulo(left: Any, right: Any) -> Any:
    if _number(right) == 0:
        raise DeriveError("modulo by zero")
    return _number(left) % _number(right)


_BINARY: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: _number(a) + _number(b),
    ast.Sub: lambda a, b: _number(a) - _number(b),
    ast.Mult: lambda a, b: _number(a) * _number(b),
    ast.Div: _divide,
    ast.Mod: _modulo,
    ast.Pow: _pow,
}
_ORDER: dict[type, Callable[[Any, Any], bool]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


@dataclass(frozen=True)
class Expression:
    source: str
    tree: ast.Expression

    def names(self) -> set[str]:
        return {node.id for node in ast.walk(self.tree) if isinstance(node, ast.Name)} - set(FUNCTIONS)


def parse_derive(source: Any) -> Expression:
    if isinstance(source, bool) or not isinstance(source, (str, int, float)):
        raise DeriveError("an expression must be text or a number")
    text = str(source).strip()
    if not text or len(text) > MAX_SOURCE_CHARS:
        raise DeriveError("an expression must be between 1 and 2000 characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise DeriveError(f"'{text}' is not a valid expression") from None
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise DeriveError(f"'{type(node).__name__}' is not allowed in an expression")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
                raise DeriveError("only the listed functions can be called")
            _, low, high = FUNCTIONS[node.func.id]
            if not low <= len(node.args) <= high:
                raise DeriveError(f"{node.func.id} takes {low if low == high else f'{low} to {high}'} values")
        if isinstance(node, ast.Attribute) and not isinstance(node.value, ast.Name):
            raise DeriveError("only one level of attributes is allowed")
        if isinstance(node, (ast.Attribute, ast.Name)):
            name = node.attr if isinstance(node, ast.Attribute) else node.id
            if name.startswith("_"):
                raise DeriveError("names cannot start with an underscore")
        if isinstance(node, ast.Compare) and len(node.ops) != 1:
            raise DeriveError("compare two values at a time")
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, str, bool)):
                raise DeriveError("unsupported literal")
            if isinstance(node.value, str) and len(node.value) > MAX_STRING_CHARS:
                raise DeriveError("a text literal is too long")
    return Expression(text, tree)


def _truthy(value: Any) -> bool:
    return bool(value)


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    return _number(left) == _number(right)


def _evaluate(node: ast.AST, env: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        raise DeriveError(f"unknown name '{node.id}'")
    if isinstance(node, ast.Attribute):
        owner = _evaluate(node.value, env)
        if not isinstance(owner, Mapping) or node.attr not in owner:
            raise DeriveError(f"unknown value '{node.value.id}.{node.attr}'")
        return owner[node.attr]
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not _truthy(operand)
        number = _number(operand)
        return -number if isinstance(node.op, ast.USub) else +number
    if isinstance(node, ast.BinOp):
        return _BINARY[type(node.op)](_evaluate(node.left, env), _evaluate(node.right, env))
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_truthy(_evaluate(value, env)) for value in node.values)
        return any(_truthy(_evaluate(value, env)) for value in node.values)
    if isinstance(node, ast.Compare):
        left, right = _evaluate(node.left, env), _evaluate(node.comparators[0], env)
        comparison = node.ops[0]
        if isinstance(comparison, ast.Eq):
            return _equal(left, right)
        if isinstance(comparison, ast.NotEq):
            return not _equal(left, right)
        return _ORDER[type(comparison)](_number(left), _number(right))
    if isinstance(node, ast.IfExp):
        branch = node.body if _truthy(_evaluate(node.test, env)) else node.orelse
        return _evaluate(branch, env)
    if isinstance(node, ast.Call):
        function = FUNCTIONS[node.func.id][0]
        return function(*(_evaluate(argument, env) for argument in node.args))
    raise DeriveError(f"'{type(node).__name__}' is not allowed in an expression")


def evaluate(expression: Expression | str, env: Mapping[str, Any]) -> Any:
    parsed = expression if isinstance(expression, Expression) else parse_derive(expression)
    try:
        value = _evaluate(parsed.tree, env)
    except DeriveError:
        raise
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise DeriveError(f"'{parsed.source}' could not be evaluated: {exc}") from None
    if isinstance(value, float):
        _finite(value)
    if isinstance(value, Mapping):
        raise DeriveError(f"'{parsed.source}' must give a number, a flag or a word")
    return value


def evaluate_all(entries: Iterable[tuple[str, Expression]], env: Mapping[str, Any]) -> dict[str, Any]:
    scope = dict(env)
    results: dict[str, Any] = {}
    for name, expression in entries:
        try:
            value = evaluate(expression, scope)
        except DeriveError as exc:
            raise DeriveError(f"{name}: {exc}") from None
        scope[name] = value
        results[name] = value
    return results
