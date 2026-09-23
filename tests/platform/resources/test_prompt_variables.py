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

    def test_conditioned_option_shows_when_clause_and_resolve_order(self):
        lines = render_prompt_variable_lines([
            {"name": "music", "type": "choice", "options": ["hip hop", "classical"]},
            {
                "name": "dance",
                "type": "choice",
                "options": [
                    {"text": "breaking", "when": {"var": "music", "values": ["hip hop"]}},
                    "salsa",
                ],
            },
        ])
        assert lines == [
            "music: one of hip hop, classical — shuffles each generation",
            "dance — resolves after $music — one of breaking (when $music = hip hop), salsa "
            "— shuffles each generation",
        ]

    def test_untouched_string_options_unaffected_by_condition_support(self):
        lines = render_prompt_variable_lines([
            {"name": "mood", "type": "choice", "options": ["noir", "sunlit"]},
        ])
        assert lines == ["mood: one of noir, sunlit — shuffles each generation"]


class TestSharedHelpers:
    def test_valid_options_normalizes_mixed_string_and_object_entries(self):
        from src.platform.resources.prompt_variables import valid_options

        options = valid_options([
            "salsa",
            {"text": "breaking", "when": {"var": "music", "values": ["hip hop"]}},
            {"text": "  "},
            "",
        ])
        assert options == [
            {"text": "salsa", "when": None},
            {"text": "breaking", "when": {"var": "music", "values": ["hip hop"]}},
        ]

    def test_variable_dependencies_dedupes_and_preserves_order(self):
        from src.platform.resources.prompt_variables import variable_dependencies

        var = {
            "type": "choice",
            "options": [
                {"text": "a", "when": {"var": "music", "values": ["x"]}},
                {"text": "b", "when": {"var": "era", "values": ["y"]}},
                {"text": "c", "when": {"var": "music", "values": ["z"]}},
            ],
        }
        assert variable_dependencies(var) == ["music", "era"]

    def test_is_downstream_detects_transitive_chain(self):
        from src.platform.resources.prompt_variables import is_downstream

        variables = {
            "music": {"type": "choice", "options": ["hip hop"]},
            "dance": {
                "type": "choice",
                "options": [{"text": "a", "when": {"var": "music", "values": ["hip hop"]}}],
            },
            "era": {
                "type": "choice",
                "options": [{"text": "b", "when": {"var": "dance", "values": ["a"]}}],
            },
        }
        assert is_downstream("era", "music", variables) is True
        assert is_downstream("music", "era", variables) is False

    def test_format_option_appends_when_clause(self):
        from src.platform.resources.prompt_variables import format_option

        assert format_option({"text": "breaking", "when": None}) == "breaking"
        assert format_option(
            {"text": "breaking", "when": {"var": "music", "values": ["hip hop", "latin"]}}
        ) == "breaking (when $music = hip hop, latin)"


class TestNameError:
    def test_valid_names_pass(self):
        from src.platform.resources.prompt_variables import name_error

        assert name_error("mood") is None
        assert name_error("_mood2") is None

    def test_invalid_names_are_rejected(self):
        from src.platform.resources.prompt_variables import name_error

        assert "not a valid variable name" in name_error("1mood")
        assert "not a valid variable name" in name_error("mo od")
        assert "not a valid variable name" in name_error("m" * 61)


class TestValidateCondition:
    def test_valid_condition_normalizes(self):
        from src.platform.resources.prompt_variables import validate_condition

        variables = {"scene": {"type": "choice", "options": ["day", "night"]}}
        normalized, error = validate_condition(
            "mood", {"var": "scene", "values": ["day"]}, variables
        )
        assert error is None
        assert normalized == {"var": "scene", "values": ["day"]}

    def test_self_reference_is_rejected(self):
        from src.platform.resources.prompt_variables import validate_condition

        _, error = validate_condition("mood", {"var": "mood", "values": ["x"]}, {"mood": {}})
        assert "cannot reference" in error

    def test_reference_to_non_choice_variable_is_rejected(self):
        from src.platform.resources.prompt_variables import validate_condition

        variables = {"scene": {"type": "text", "value": "day"}}
        _, error = validate_condition("mood", {"var": "scene", "values": ["day"]}, variables)
        assert "is not a choice variable" in error

    def test_values_not_among_referenced_options_are_rejected(self):
        from src.platform.resources.prompt_variables import validate_condition

        variables = {"scene": {"type": "choice", "options": ["day", "night"]}}
        _, error = validate_condition(
            "mood", {"var": "scene", "values": ["dusk"]}, variables
        )
        assert "are not options of $scene" in error


class TestValidateVariablesMap:
    def test_none_is_valid(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        assert validate_variables_map(None) == []

    def test_non_dict_is_rejected(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        assert validate_variables_map([1, 2]) != []

    def test_valid_text_and_choice_map_has_no_errors(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        variables = {
            "mood": {"type": "text", "value": "noir"},
            "scene": {
                "type": "choice", "mode": "pin", "pinnedIndex": 0,
                "options": ["day", "night"],
            },
        }
        assert validate_variables_map(variables) == []

    def test_bad_name_is_reported(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        errors = validate_variables_map({"1bad": {"type": "text", "value": "x"}})
        assert any("not a valid variable name" in e for e in errors)

    def test_choice_with_no_options_is_rejected(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        errors = validate_variables_map({"scene": {"type": "choice", "options": []}})
        assert any("needs at least one non-empty option" in e for e in errors)

    def test_invalid_mode_is_rejected(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        errors = validate_variables_map({
            "scene": {"type": "choice", "mode": "random", "options": ["day"]}
        })
        assert any("invalid mode" in e for e in errors)

    def test_pinned_index_out_of_range_is_rejected(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        errors = validate_variables_map({
            "scene": {"type": "choice", "mode": "pin", "pinnedIndex": 5, "options": ["day"]}
        })
        assert any("pinnedIndex must be a valid index" in e for e in errors)

    def test_unknown_definition_type_is_rejected(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        errors = validate_variables_map({"mood": {"type": "number", "value": 1}})
        assert any("Use text or choice" in e for e in errors)

    def test_condition_referencing_a_variable_outside_the_map_is_rejected(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        errors = validate_variables_map({
            "scene": {
                "type": "choice", "mode": "shuffle", "options": [
                    {"text": "x", "when": {"var": "missing", "values": ["y"]}}
                ],
            },
        })
        assert any("is not a choice variable" in e for e in errors)

    def test_choice_options_and_condition_values_are_uncapped(self):
        from src.platform.resources.prompt_variables import validate_variables_map

        scenes = [f"scene {i}" for i in range(40)]
        variables = {
            "scene": {"type": "choice", "mode": "shuffle", "options": scenes},
            "outfit": {
                "type": "choice", "mode": "shuffle", "options": [
                    {"text": f"outfit {i}", "when": {"var": "scene", "values": scenes[:20]}}
                    for i in range(40)
                ],
            },
        }
        assert validate_variables_map(variables) == []

    def test_too_many_variables_is_rejected(self):
        from src.platform.resources.prompt_variables import validate_variables_map, MAX_VARIABLES

        variables = {f"v{i}": {"type": "text", "value": "x"} for i in range(MAX_VARIABLES + 1)}
        errors = validate_variables_map(variables)
        assert any("Too many prompt variables" in e for e in errors)
