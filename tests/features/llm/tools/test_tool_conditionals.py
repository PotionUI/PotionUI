"""Tests for render_tool_conditionals — the {{#if}}/{{#ifany}} primitive."""

from src.features.llm.tools.tool_conditionals import render_tool_conditionals


class TestIfBlock:
    def test_kept_when_tool_allowed(self):
        out = render_tool_conditionals("a{{#if x}} b{{/if}} c", ["x"])
        assert out == "a b c"

    def test_dropped_when_tool_absent(self):
        out = render_tool_conditionals("a{{#if x}} b{{/if}} c", ["y"])
        assert out == "a c"

    def test_if_requires_all_names(self):
        tmpl = "start{{#if x y}} both{{/if}} end"
        assert render_tool_conditionals(tmpl, ["x", "y"]) == "start both end"
        assert render_tool_conditionals(tmpl, ["x"]) == "start end"


class TestIfAnyBlock:
    def test_kept_when_any_allowed(self):
        tmpl = "{{#ifany x y}}body{{/ifany}}"
        assert render_tool_conditionals(tmpl, ["y"]) == "body"

    def test_dropped_when_none_allowed(self):
        tmpl = "{{#ifany x y}}body{{/ifany}}"
        assert render_tool_conditionals(tmpl, ["z"]) == ""


class TestNesting:
    """The reduced-form idiom: a joined sentence that collapses cleanly."""

    TMPL = (
        "{{#ifany a b}}Call"
        "{{#if a}} a{{/if}}"
        "{{#if b}}{{#if a}} and{{/if}} b{{/if}}"
        " first.{{/ifany}}"
    )

    def test_both(self):
        assert render_tool_conditionals(self.TMPL, ["a", "b"]) == "Call a and b first."

    def test_only_first(self):
        assert render_tool_conditionals(self.TMPL, ["a"]) == "Call a first."

    def test_only_second(self):
        assert render_tool_conditionals(self.TMPL, ["b"]) == "Call b first."

    def test_neither(self):
        assert render_tool_conditionals(self.TMPL, []) == ""


class TestAllowedNone:
    def test_none_keeps_every_block_and_strips_markers(self):
        tmpl = "x{{#if a}} a{{/if}}{{#ifany b}} b{{/ifany}}"
        assert render_tool_conditionals(tmpl, None) == "x a b"


class TestCleanup:
    def test_no_marker_is_a_noop(self):
        assert render_tool_conditionals("plain text", ["a"]) == "plain text"

    def test_empty_input(self):
        assert render_tool_conditionals("", ["a"]) == ""

    def test_dropped_blocks_do_not_leave_blank_line_runs(self):
        tmpl = "top\n\n{{#if a}}middle\n\n{{/if}}bottom"
        # 'a' absent: the middle paragraph vanishes without leaving 3+ newlines.
        assert "\n\n\n" not in render_tool_conditionals(tmpl, [])

    def test_trailing_spaces_from_a_dropped_block_are_trimmed(self):
        tmpl = "line {{#if a}}kept{{/if}}\nnext"
        assert render_tool_conditionals(tmpl, []) == "line\nnext"
