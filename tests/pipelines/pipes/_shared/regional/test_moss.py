import json
from pathlib import Path

import pytest

from src.pipelines.pipes._shared.regional import (
    RegionalItem,
    RegionalPromptDocument,
    compile_moss_prompt,
    normalize_regional_prompt,
    parse_resolution,
    pixel_box,
    quoted_spans,
)

FIXTURE = Path(__file__).resolve().parents[4] / "fixtures" / "regional_prompt" / "moss_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fixture_case_is_reproduced_byte_for_byte(case):
    document = normalize_regional_prompt({"enabled": True, "items": case["items"]})
    result = compile_moss_prompt(case["caption"], document, case["width"], case["height"])
    assert result == case["expected"]


def test_fixture_is_not_empty():
    assert len(CASES) >= 4


class TestNormalize:
    def test_none_is_a_disabled_empty_document(self):
        assert normalize_regional_prompt(None) == RegionalPromptDocument()

    def test_fractions_are_clamped_to_unit_range(self):
        doc = normalize_regional_prompt(
            {"enabled": True, "items": [{"x": -0.5, "y": 2, "w": 3, "h": 0.5, "caption": "a"}]}
        )
        item = doc.items[0]
        assert (item.x, item.y, item.w, item.h) == (0.0, 1.0, 1.0, 0.5)

    @pytest.mark.parametrize("w,h", [(0, 0.5), (0.5, 0), (-1, 0.5)])
    def test_zero_or_negative_size_is_rejected(self, w, h):
        with pytest.raises(ValueError):
            normalize_regional_prompt({"enabled": True, "items": [{"x": 0, "y": 0, "w": w, "h": h, "caption": "a"}]})

    @pytest.mark.parametrize("bad", [
        {"enabled": "yes", "items": []},
        {"enabled": True, "items": "nope"},
        {"enabled": True, "items": ["nope"]},
        {"enabled": True, "items": [{"x": 0, "y": 0, "w": 1, "h": 1, "caption": 3}]},
        {"enabled": True, "items": [{"x": "0", "y": 0, "w": 1, "h": 1, "caption": "a"}]},
        {"enabled": True, "items": [{"x": float("nan"), "y": 0, "w": 1, "h": 1, "caption": "a"}]},
        [],
    ])
    def test_malformed_documents_are_rejected(self, bad):
        with pytest.raises(ValueError):
            normalize_regional_prompt(bad)


class TestGeometry:
    def test_edges_within_tolerance_of_an_integer_snap_to_it(self):
        item = RegionalItem(x=0.1, y=0, w=0.8, h=1, caption="a")
        assert pixel_box(item, 1000, 10) == (100, 0, 900, 10)

    def test_left_top_floor_and_right_bottom_ceil(self):
        item = RegionalItem(x=0.1, y=0.1, w=0.8, h=0.8, caption="a")
        assert pixel_box(item, 1024, 1024) == (102, 102, 922, 922)

    def test_box_running_past_the_canvas_is_clipped(self):
        item = RegionalItem(x=0.9, y=0.9, w=0.5, h=0.5, caption="a")
        assert pixel_box(item, 100, 100) == (90, 90, 100, 100)

    @pytest.mark.parametrize("value,expected", [("1024x768", (1024, 768)), (" 512X512 ", (512, 512)), ("640×480", (640, 480))])
    def test_resolution_parses(self, value, expected):
        assert parse_resolution(value) == expected

    @pytest.mark.parametrize("value", ["", None, "big", "0x10", "10x"])
    def test_bad_resolution_is_rejected(self, value):
        with pytest.raises(ValueError):
            parse_resolution(value)


class TestQuotedSpans:
    def test_straight_and_curly_quotes(self):
        assert quoted_spans('Sign "OPEN" and “DAILY”') == ["OPEN", "DAILY"]

    def test_empty_and_blank_spans_are_ignored(self):
        assert quoted_spans('a "" b "  " c') == []

    def test_no_quotes(self):
        assert quoted_spans("plain") == []


class TestCompile:
    def _doc(self, *captions, enabled=True):
        return RegionalPromptDocument(
            enabled=enabled,
            items=[RegionalItem(0.0, 0.0, 0.5, 0.5, c) for c in captions],
        )

    def test_disabled_returns_the_prompt_unchanged(self):
        assert compile_moss_prompt("p", self._doc("a", enabled=False), 512, 512) == "p"

    def test_no_items_returns_the_prompt_unchanged(self):
        assert compile_moss_prompt("p", self._doc(), 512, 512) == "p"

    def test_blank_captions_return_the_prompt_unchanged(self):
        assert compile_moss_prompt("p", self._doc("  ", ""), 512, 512) == "p"

    def test_blank_items_are_dropped_beside_real_ones(self):
        result = json.loads(compile_moss_prompt("p", self._doc("", "cat"), 512, 512))
        assert [i["caption"] for i in result["items"]] == ["cat"]

    def test_braces_in_the_caption_survive_json_encoding(self):
        result = json.loads(compile_moss_prompt("{a|b}", self._doc("x"), 512, 512))
        assert result["summary"]["caption"] == "{a|b}"

    def test_item_key_order_for_text_items(self):
        result = json.loads(compile_moss_prompt("p", self._doc('say "HI"'), 512, 512))
        assert list(result["items"][0]) == ["label", "caption", "text", "bbox"]
