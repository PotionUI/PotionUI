from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.platform.media_tools.effects.derive import DeriveError, evaluate, evaluate_all, parse_derive

ENV = {
    "amount": 60,
    "size": 1.5,
    "on": True,
    "easing": "smooth",
    "media": {"width": 640, "height": 360, "fps": 24.0, "duration": 4.0},
    "anchor": {"x": 0.5, "y": 0.0},
}


@pytest.mark.parametrize(
    "source, expected",
    [
        ("amount / 100 * 0.14 * 255", 60 / 100 * 0.14 * 255),
        ("max(1, floor(size * min(media.width, media.height) / 1000 + 0.5))", 1),
        ("-2 ** 2", -4),
        ("2 ** 3 ** 2", 512),
        ("-1 % 3", 2),
        ("7 % -3", -2),
        ("round(2.5)", 2),
        ("round(3.5)", 4),
        ("round(-2.5)", -2),
        ("round(2.675, 2)", 2.67),
        ("round(1350, -2)", 1400),
        ("floor(-0.5)", -1),
        ("clamp(5, 0, 1)", 1),
        ("clamp(-5, 0, 1)", 0),
        ("abs(-3)", 3),
        ("pow(2, 10)", 1024),
        ("sqrt(16)", 4),
        ("cos(0) + sin(0)", 1),
        ("pi", math.pi),
        ("easing == 'smooth'", True),
        ("easing != 'smooth'", False),
        ("1 if on else 2", 1),
        ("on and amount > 50", True),
        ("not on or amount < 50", False),
        ("on + 1", 2),
        ("anchor.x + anchor.y", 0.5),
        ("max(1, 2, 3)", 3),
        ("0 < amount < 100", True),
        ("'a' < 'b'", True),
        ("1e3", 1000.0),
    ],
)
def test_the_language_evaluates_like_the_spec(source, expected):
    assert evaluate(source, ENV) == pytest.approx(expected)


def test_logic_returns_the_deciding_operand_like_python():
    assert evaluate("amount and size", ENV) == 1.5
    assert evaluate("0 or ''", ENV) == ""
    assert evaluate("not amount", ENV) is False
    assert evaluate("0 and missing", ENV) == 0


def test_text_joins_and_compares_but_never_mixes_with_numbers():
    assert evaluate("'ab' + 'cd'", ENV) == "abcd"
    assert evaluate("easing == 1", ENV) is False
    for source in ("easing + 1", "easing < 1", "max('a', 1)"):
        with pytest.raises(DeriveError):
            evaluate(source, ENV)


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os')",
        "open('x')",
        "media.width.real",
        "[1, 2]",
        "{'a': 1}",
        "amount[0]",
        "lambda: 1",
        "(x for x in amount)",
        "f'{amount}'",
        "amount if True",
        "min(amount, key=1)",
        "min(*amount)",
        "amount is None",
        "1 in media",
        "amount << 2",
        "amount // 2",
        "None",
        "b'x'",
        "x := 1",
        "'" + "a" * 100 + "'",
        "",
        "a" * 2001,
    ],
)
def test_anything_outside_the_whitelist_is_refused(source):
    with pytest.raises(DeriveError):
        parse_derive(source)


@pytest.mark.parametrize(
    "source",
    [
        "1 / 0",
        "1 % 0",
        "sqrt(-1)",
        "pow(10, 2000)",
        "pow(0, -1)",
        "unknown + 1",
        "media.depth",
        "easing + 1",
        "floor(pow(10, 300) * pow(10, 300))",
        "media",
        "(-8) ** 0.5",
        "1e400",
        "10 ** 400",
        "_secret",
        "media.__proto__",
        "amount.__class__",
        "constructor",
        "clamp(1, 2)",
        "min(1)",
        "max()",
        "round(1.5, 'a')",
    ],
)
def test_runtime_errors_are_derive_errors(source):
    with pytest.raises(DeriveError):
        evaluate(source, ENV)


def test_entries_evaluate_in_order_and_later_ones_see_earlier_ones():
    entries = [(name, parse_derive(source)) for name, source in (("k", "amount / 100"), ("twice", "k * 2"))]

    assert evaluate_all(entries, ENV) == {"k": pytest.approx(0.6), "twice": pytest.approx(1.2)}


def test_a_failing_entry_names_itself():
    entries = [("bad", parse_derive("amount / 0"))]

    with pytest.raises(DeriveError, match="^bad: "):
        evaluate_all(entries, ENV)


def test_names_lists_the_values_an_expression_reads_without_functions():
    assert parse_derive("max(amount, media.width) + k").names() == {"amount", "media", "k"}


GOLDENS = json.loads((Path(__file__).resolve().parents[3] / "fixtures" / "effects" / "derive" / "cases.json").read_text())


def _same(got, expected):
    if isinstance(got, (bool, str)) or isinstance(expected, (bool, str)):
        return type(got) is type(expected) and got == expected
    return abs(got - expected) <= GOLDENS["tolerance"] * max(1.0, abs(expected))


@pytest.mark.parametrize("case", GOLDENS["expressions"], ids=lambda case: case["expr"])
def test_the_shared_goldens_agree_with_the_browser_parser(case):
    try:
        parsed = parse_derive(case["expr"])
    except DeriveError:
        assert case.get("error") == "parse"
        return
    try:
        value = evaluate(parsed, case.get("env", {}))
    except DeriveError:
        assert case.get("error") == "eval"
        return
    assert "error" not in case and _same(value, case["value"])


@pytest.mark.parametrize("block", GOLDENS["blocks"], ids=lambda block: block["name"])
def test_the_shared_derive_blocks_agree_with_the_browser_parser(block):
    values = evaluate_all([(name, parse_derive(source)) for name, source in block["derive"].items()], block["env"])

    assert all(_same(values[name], expected) for name, expected in block["values"].items())


def test_underscore_names_are_never_read_even_when_present():
    for source in ("_hidden", "media._hidden"):
        with pytest.raises(DeriveError, match="unknown value"):
            evaluate(source, {"_hidden": 1, "media": {"_hidden": 1}})


@pytest.mark.parametrize(
    "source",
    [
        "-" * 1999 + "1",
        "not " * 400 + "1",
        "(" * 300 + "1" + ")" * 300,
        "abs(" * 200 + "1" + ")" * 200,
        "1 if " * 120 + "1" + " else 1" * 120,
        "2 ** " * 300 + "1",
    ],
)
def test_deep_nesting_under_the_length_cap_is_a_derive_error(source):
    assert len(source) <= 2000
    with pytest.raises(DeriveError):
        parse_derive(source)


def test_a_moderately_nested_expression_still_parses():
    assert evaluate("-" * 30 + "1", ENV) == 1


def test_evaluating_a_tree_deeper_than_the_stack_is_a_derive_error():
    import ast

    from src.platform.media_tools.effects.derive import Expression

    node = ast.Constant(1)
    for _ in range(20000):
        node = ast.UnaryOp(op=ast.USub(), operand=node)

    with pytest.raises(DeriveError, match="too deep"):
        evaluate(Expression("deep", ast.Expression(body=node)), ENV)


@pytest.mark.parametrize("error", [RecursionError, MemoryError])
def test_a_parser_that_runs_out_of_stack_or_memory_is_a_derive_error(monkeypatch, error):
    from src.platform.media_tools.effects import derive

    def exhausted(*args, **kwargs):
        raise error()

    monkeypatch.setattr(derive, "_syntax", exhausted)

    with pytest.raises(DeriveError, match="not a valid expression"):
        parse_derive("1 + 1")
