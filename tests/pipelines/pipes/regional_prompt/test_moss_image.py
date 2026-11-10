import json
from pathlib import Path

import pytest

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.regional_prompt.moss_image import RegionalPromptMossImagePipe

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "regional_prompt" / "moss_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

ITEM = {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5, "caption": "a cat"}


def _run(pairs, document, resolution="512x512"):
    config = RegionalPromptMossImagePipe.get_default_config()
    config.update({"pairs": pairs, "regional_prompt": document, "resolution": resolution})
    output = RegionalPromptMossImagePipe(config).process(PipeInput(input={}), lambda o: None)
    return output.output["pairs"]


def test_fixture_case_with_an_odd_canvas_through_the_pipe():
    case = CASES[-1]
    pairs = _run(
        [{"positive": case["caption"], "negative": "bad"}],
        {"enabled": True, "items": case["items"]},
        f"{case['width']}x{case['height']}",
    )
    assert pairs[0]["positive"] == case["expected"]
    assert pairs[0]["negative"] == "bad"


def test_declared_contract():
    assert RegionalPromptMossImagePipe.name == "regional_prompt"
    assert {s.name for s in RegionalPromptMossImagePipe.configuration()} == {"pairs", "regional_prompt", "resolution"}
    (out,) = RegionalPromptMossImagePipe.outputs()
    assert (out.name, out.io_type, out.is_array) == ("pairs", IOType.DICT, True)


@pytest.mark.parametrize("document", [
    {"enabled": False, "items": [ITEM]},
    {"enabled": True, "items": []},
    {"enabled": True, "items": [{**ITEM, "caption": "   "}]},
    {},
])
def test_prompt_is_unchanged_when_nothing_applies(document):
    pairs = [{"positive": "p", "negative": "n"}]
    assert _run(pairs, document) == pairs


def test_negative_is_never_touched():
    (pair,) = _run([{"positive": "p", "negative": "{blur}"}], {"enabled": True, "items": [ITEM]})
    assert pair["negative"] == "{blur}"
    assert json.loads(pair["positive"])["summary"]["caption"] == "p"


def test_each_pair_is_compiled_with_its_own_expanded_caption():
    pairs = _run(
        [{"positive": "red car", "negative": ""}, {"positive": "blue car", "negative": ""}],
        {"enabled": True, "items": [ITEM]},
    )
    assert [json.loads(p["positive"])["summary"]["caption"] for p in pairs] == ["red car", "blue car"]


def test_input_pairs_are_not_mutated():
    source = [{"positive": "p", "negative": "n"}]
    _run(source, {"enabled": True, "items": [ITEM]})
    assert source == [{"positive": "p", "negative": "n"}]


def test_invalid_document_raises():
    with pytest.raises(ValueError):
        _run([{"positive": "p", "negative": ""}], {"enabled": True, "items": [{**ITEM, "w": 0}]})


@pytest.mark.parametrize("document", [
    {"enabled": True, "items": [{**ITEM, "x": "left"}]},
    {"enabled": "yes", "items": [ITEM]},
    {"enabled": True, "items": "nope"},
])
def test_malformed_document_raises_when_enabled(document):
    with pytest.raises(ValueError):
        _run([{"positive": "p", "negative": ""}], document)


def test_malformed_resolution_raises_when_enabled():
    with pytest.raises(ValueError):
        _run([{"positive": "p", "negative": ""}], {"enabled": True, "items": [ITEM]}, "big")


def test_malformed_resolution_is_ignored_when_disabled():
    pairs = [{"positive": "p", "negative": "n"}]
    assert _run(pairs, {"enabled": False, "items": [ITEM]}, "big") == pairs
