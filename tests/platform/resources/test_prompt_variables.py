"""Tests for the shared prompt-variable plain-language renderer."""

from src.platform.resources.prompt_variables import render_prompt_variable_lines


class TestRenderPromptVariableLines:
    def test_absent_or_malformed_returns_empty(self):
        assert render_prompt_variable_lines(None) == []
        assert render_prompt_variable_lines("nope") == []
        assert render_prompt_variable_lines({}) == []
        assert render_prompt_variable_lines([]) == []
        assert render_prompt_variable_lines([123, "x", None]) == []

    def test_text_variable_with_value(self):
        lines = render_prompt_variable_lines(
            [{"name": "subject", "type": "text", "value": "a red fox"}]
        )
        assert lines == ["subject: a red fox"]

    def test_text_variable_empty(self):
        lines = render_prompt_variable_lines([{"name": "subject", "type": "text"}])
        assert lines == ["subject: free text (empty)"]

    def test_choice_shuffle_with_last_roll(self):
        lines = render_prompt_variable_lines([{
            "name": "mood",
            "type": "choice",
            "options": ["noir", "sunlit"],
            "mode": "shuffle",
            "lastRoll": "sunlit",
        }])
        assert lines == ["mood: one of noir, sunlit — shuffles each generation; last roll: sunlit"]

    def test_choice_default_mode_is_shuffle(self):
        lines = render_prompt_variable_lines([{
            "name": "mood", "type": "choice", "options": ["a", "b"],
        }])
        assert lines == ["mood: one of a, b — shuffles each generation"]

    def test_choice_pin_names_the_pinned_option(self):
        lines = render_prompt_variable_lines([{
            "name": "mood", "type": "choice", "options": ["noir", "sunlit"],
            "mode": "pin", "pinnedIndex": 1,
        }])
        assert lines == ["mood: one of noir, sunlit — pinned to sunlit"]

    def test_choice_pin_out_of_range_falls_back(self):
        lines = render_prompt_variable_lines([{
            "name": "mood", "type": "choice", "options": ["noir"],
            "mode": "pin", "pinnedIndex": 9,
        }])
        assert lines == ["mood: one of noir — pinned"]

    def test_choice_per_image(self):
        lines = render_prompt_variable_lines([{
            "name": "mood", "type": "choice", "options": ["a", "b"], "mode": "per-image",
        }])
        assert lines == ["mood: one of a, b — re-rolls independently per image"]

    def test_choice_with_no_valid_options_skipped(self):
        lines = render_prompt_variable_lines([
            {"name": "empty", "type": "choice", "options": ["", "  "]},
            {"name": "ok", "type": "text", "value": "x"},
        ])
        assert lines == ["ok: x"]

    def test_nameless_entries_skipped(self):
        lines = render_prompt_variable_lines([
            {"type": "text", "value": "x"},
            {"name": "  ", "type": "text", "value": "y"},
            {"name": "keep", "type": "text", "value": "z"},
        ])
        assert lines == ["keep: z"]

    def test_variable_count_cap(self):
        variables = [
            {"name": f"v{i}", "type": "text", "value": str(i)} for i in range(40)
        ]
        lines = render_prompt_variable_lines(variables)
        assert len(lines) == 24  # _MAX_VARIABLES

    def test_option_count_cap_adds_ellipsis(self):
        options = [f"opt{i}" for i in range(20)]
        lines = render_prompt_variable_lines([{
            "name": "big", "type": "choice", "options": options, "mode": "per-image",
        }])
        assert lines[0].startswith("big: one of opt0, ")
        assert ", …" in lines[0]
        # only the first 12 options are named
        assert "opt11" in lines[0]
        assert "opt12" not in lines[0]

    def test_long_value_clipped(self):
        lines = render_prompt_variable_lines([{
            "name": "v", "type": "text", "value": "x" * 500,
        }])
        assert lines[0].endswith("…")
        assert len(lines[0]) < 120
