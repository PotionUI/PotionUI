import json
import re
from pathlib import Path

import pytest

from src.features.sessions.request import (
    LAYOUT_KEYS,
    ENTRY_KEYS_HANDLED,
    ENTRY_KEYS_IGNORED,
    ENTRY_KEYS_REFUSED,
    Randomness,
    RequestRefused,
    resolve,
)
from src.features.sessions.request_variables import normalize_variable_def

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "session_requests"
FILES = sorted(FIXTURES.glob("*.json"))


def sequence(values):
    state = {"index": 0}

    def next_value():
        value = values[state["index"] % len(values)]
        state["index"] += 1
        return value

    return next_value


def run(fixture):
    return resolve(
        fixture["entry"],
        "preset-fixture",
        fixture["mode"],
        fixture["preset_vars"],
        fixture["overrides"],
        Randomness(random=sequence(fixture["random"]), now=lambda: 0),
        fixture["can_batch"],
        fixture["tab_id"],
    )


def test_the_fixture_directory_is_not_empty():
    assert len(FILES) >= 20


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_resolver_equals_the_frontend_reference_on_every_fixture(path):
    fixture = json.loads(path.read_text())
    if fixture.get("refusal"):
        with pytest.raises(RequestRefused) as refused:
            run(fixture)
        assert refused.value.code == fixture["refusal"]
        return
    if "expected_refusal" in fixture:
        with pytest.raises(RequestRefused) as refused:
            run(fixture)
        assert refused.value.code == fixture["expected_refusal"]
        return
    assert json.loads(json.dumps(run(fixture).request)) == fixture["expected"]


def test_every_fixture_is_either_expected_or_refused():
    for path in FILES:
        fixture = json.loads(path.read_text())
        assert sum(key in fixture for key in ("expected", "expected_refusal", "refusal")) == 1, path.name


def test_the_fixtures_exercise_each_variable_kind_and_mode():
    seen = set()
    for path in FILES:
        entry = json.loads(path.read_text())["entry"]
        for stored in (entry.get("variables") or {}).values():
            definition = normalize_variable_def(stored)
            seen.add((definition["type"], definition.get("mode")))
    assert {
        ("text", None),
        ("choice", "shuffle"),
        ("choice", "pin"),
        ("choice", "per-image"),
        ("number", "shuffle"),
        ("number", "pin"),
        ("number", "per-image"),
    } <= seen


def session_data_keys():
    source = (ROOT / "frontend" / "src" / "lib" / "types" / "api.ts").read_text()
    body = re.search(r"export interface SessionData \{(.*?)\n\}", source, re.S).group(1)
    return set(re.findall(r"^\t([A-Za-z]+)\??:", body, re.M))


def test_every_key_the_frontend_saves_in_a_session_is_classified_here():
    classified = ENTRY_KEYS_HANDLED | ENTRY_KEYS_IGNORED | ENTRY_KEYS_REFUSED | LAYOUT_KEYS
    unclassified = session_data_keys() - classified
    assert not unclassified, f"new session fields the resolver has not decided about: {sorted(unclassified)}"


def test_the_resolver_does_not_classify_keys_the_frontend_no_longer_saves():
    classified = ENTRY_KEYS_HANDLED | ENTRY_KEYS_IGNORED | ENTRY_KEYS_REFUSED
    extra = {key for key in classified - session_data_keys() if key not in {"autoTagIds", "autoCollectionIds", "sourcePromptId"}}
    assert not extra, sorted(extra)


def variable_def_kinds():
    source = (ROOT / "frontend" / "src" / "lib" / "utils" / "variableDefs.ts").read_text()
    kinds = set(re.findall(r"^\ttype: '([a-z]+)';", source, re.M))
    return kinds


def test_every_variable_kind_the_frontend_defines_is_handled():
    assert variable_def_kinds() == {"text", "choice", "number"}
