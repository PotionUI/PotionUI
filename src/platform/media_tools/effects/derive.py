from __future__ import annotations

import ast
import math
import operator
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

MAX_SOURCE_CHARS = 2000
MAX_STRING_CHARS = 64
MAX_EXPONENT = 1024.0


class DeriveError(ValueError):
    pass


def _real(value: Any) -> Any:
    if isinstance(value, complex):
        raise DeriveError("the expression does not give a real number")
    if isinstance(value, float) and not math.isfinite(value):
        raise DeriveError("the expression does not give a finite number")
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > sys.float_info.max:
        raise DeriveError("the expression does not give a finite number")
    return value


def _number(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return value
    raise DeriveError(f"a {type(value).__name__} is not a number")


def _arity(name: str, values: tuple, low: int, high: int) -> None:
    if not low <= len(values) <= high:
        count = low if low == high else f"{low} to {high}"
        raise DeriveError(f"{name} takes {count} values")


def _pow(base: Any, exponent: Any) -> Any:
    left, right = _number(base), _number(exponent)
    if abs(right) > MAX_EXPONENT:
        raise DeriveError("the exponent is too large")
    try:
        return _real(left ** right)
    except (OverflowError, ZeroDivisionError):
        raise DeriveError("the power is out of range") from None


def _ordered(values: tuple) -> tuple:
    if all(isinstance(value, str) for value in values):
        return values
    return tuple(_number(value) for value in values)


def _min(*values: Any) -> Any:
    _arity("min", values, 2, 16)
    return min(_ordered(values))


def _max(*values: Any) -> Any:
    _arity("max", values, 2, 16)
    return max(_ordered(values))


def _clamp(*values: Any) -> Any:
    _arity("clamp", values, 3, 3)
    value, low, high = (_number(value) for value in values)
    return max(low, min(high, value))


def _round(*values: Any) -> Any:
    _arity("round", values, 1, 2)
    number = _number(values[0])
    if len(values) == 1:
        return round(_real(float(number)) if isinstance(number, float) else number)
    digits = values[1]
    if isinstance(digits, bool) or not isinstance(digits, int):
        raise DeriveError("round takes a whole number of digits")
    return round(number, digits)


def _unary(name: str, function: Callable[[Any], Any]) -> Callable:
    def apply(*values: Any) -> Any:
        _arity(name, values, 1, 1)
        try:
            return function(_number(values[0]))
        except (ValueError, OverflowError):
            raise DeriveError(f"{name} is out of range") from None

    return apply


def _pow_function(*values: Any) -> Any:
    _arity("pow", values, 2, 2)
    return _pow(*values)


FUNCTIONS: dict[str, Callable] = {
    "min": _min,
    "max": _max,
    "clamp": _clamp,
    "abs": _unary("abs", abs),
    "floor": _unary("floor", lambda value: math.floor(_real(value))),
    "round": _round,
    "sqrt": _unary("sqrt", math.sqrt),
    "sin": _unary("sin", math.sin),
    "cos": _unary("cos", math.cos),
    "pow": _pow_function,
}
CONSTANTS = {"pi": math.pi}
RESERVED = frozenset({"media", "time", "pi", *FUNCTIONS})

_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.IfExp, ast.Call, ast.Name,
    ast.Attribute, ast.Constant, ast.Load, ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, ast.Add, ast.Sub,
    ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def _add(left: Any, right: Any) -> Any:
    if isinstance(left, str) and isinstance(right, str):
        if len(left) + len(right) > MAX_STRING_CHARS:
            raise DeriveError("a text value is too long")
        return left + right
    return _number(left) + _number(right)


def _divide(left: Any, right: Any) -> float:
    if _number(right) == 0:
        raise DeriveError("division by zero")
    return _number(left) / right


def _modulo(left: Any, right: Any) -> Any:
    if _number(right) == 0:
        raise DeriveError("modulo by zero")
    return _number(left) % right


_BINARY: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: _add,
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
            if any(isinstance(argument, ast.Starred) for argument in node.args):
                raise DeriveError("only the listed functions can be called")
        if isinstance(node, ast.Attribute) and not isinstance(node.value, ast.Name):
            raise DeriveError("only one level of attributes is allowed")
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


def _order(comparison: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(comparison, ast.Eq):
        return _equal(left, right)
    if isinstance(comparison, ast.NotEq):
        return not _equal(left, right)
    if isinstance(left, str) != isinstance(right, str):
        raise DeriveError("text and numbers cannot be ordered")
    if not isinstance(left, str):
        _number(left), _number(right)
    return _ORDER[type(comparison)](left, right)


def _lookup(scope: Mapping[str, Any], name: str, label: str) -> Any:
    if name.startswith("_") or name not in scope:
        raise DeriveError(f"unknown value '{label}'")
    return scope[name]


def _evaluate(node: ast.AST, env: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env and node.id in CONSTANTS:
            return CONSTANTS[node.id]
        return _lookup(env, node.id, node.id)
    if isinstance(node, ast.Attribute):
        owner = _evaluate(node.value, env)
        label = f"{node.value.id}.{node.attr}"
        if not isinstance(owner, Mapping):
            raise DeriveError(f"unknown value '{label}'")
        return _lookup(owner, node.attr, label)
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not _truthy(operand)
        number = _number(operand)
        return -number if isinstance(node.op, ast.USub) else +number
    if isinstance(node, ast.BinOp):
        return _BINARY[type(node.op)](_evaluate(node.left, env), _evaluate(node.right, env))
    if isinstance(node, ast.BoolOp):
        stop = not isinstance(node.op, ast.And)
        value = None
        for operand in node.values:
            value = _evaluate(operand, env)
            if _truthy(value) is stop:
                return value
        return value
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, env)
        for comparison, operand in zip(node.ops, node.comparators):
            right = _evaluate(operand, env)
            if not _order(comparison, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        branch = node.body if _truthy(_evaluate(node.test, env)) else node.orelse
        return _evaluate(branch, env)
    if isinstance(node, ast.Call):
        return FUNCTIONS[node.func.id](*(_evaluate(argument, env) for argument in node.args))
    raise DeriveError(f"'{type(node).__name__}' is not allowed in an expression")


def evaluate(expression: Expression | str, env: Mapping[str, Any]) -> Any:
    parsed = expression if isinstance(expression, Expression) else parse_derive(expression)
    try:
        value = _evaluate(parsed.tree, env)
    except DeriveError:
        raise
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise DeriveError(f"'{parsed.source}' could not be evaluated: {exc}") from None
    _real(value)
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
