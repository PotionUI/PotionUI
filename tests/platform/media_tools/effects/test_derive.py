from __future__ import annotations

import math

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
        ("round(2.5)", 3),
        ("round(-2.5)", -2),
        ("round(0.49999)", 0),
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
        ("1e3", 1000.0),
    ],
)
def test_the_language_evaluates_like_the_spec(source, expected):
    assert evaluate(source, ENV) == pytest.approx(expected)


def test_logic_returns_booleans_not_operands():
    assert evaluate("amount and size", ENV) is True
    assert evaluate("0 or ''", ENV) is False


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os')",
        "open('x')",
        "amount.__class__",
        "media.width.real",
        "[1, 2]",
        "{'a': 1}",
        "amount[0]",
        "lambda: 1",
        "(x for x in amount)",
        "f'{amount}'",
        "amount if True",
        "min(amount, key=1)",
        "0 < amount < 100",
        "amount is None",
        "1 in media",
        "amount << 2",
        "amount // 2",
        "_secret",
        "None",
        "b'x'",
        "x := 1",
        "'" + "a" * 100 + "'",
        "clamp(1, 2)",
        "max()",
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
