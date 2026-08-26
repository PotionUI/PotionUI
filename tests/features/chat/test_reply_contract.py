"""Tests for the structured reply contract parser."""

import pytest

from src.features.chat.reply_contract import (
    REPLY_CONTRACT_PROMPT_BLOCK,
    TOOL_LOOP_CONTINUATION_NUDGE,
    parse_reply_contract,
)


class TestToolLoopContinuationNudge:
    def test_rule_present_in_prompt_block(self):
        """The static prompt states the no-narrating rule once; the hard-rules
        list must carry a matching line so a model that only ever reads the
        block (never sees the mid-loop nudge, e.g. a non-structured-reply
        mode) still gets the instruction."""
        assert "announcing an action you have not yet performed" in REPLY_CONTRACT_PROMPT_BLOCK

    def test_nudge_constant_is_non_empty_imperative_text(self):
        assert TOOL_LOOP_CONTINUATION_NUDGE
        assert "call the next tool" in TOOL_LOOP_CONTINUATION_NUDGE.lower()


class TestParseReplyContract:
    def test_both_sections(self):
        content = (
            "Anchored the subject and pruned the negative.\n\n"
            "## improved\n"
            "- subject anchored: \"lone hiker\" is now \"a lone hiker in a red parka\"\n"
            "- negative pruned: 6 redundant terms removed\n"
            "## questions\n"
            "1. keep the rain ambience or push golden hour? [rain | golden hour]\n"
            "2. anything to avoid in the background?\n"
        )
        cleaned, contract = parse_reply_contract(content)

        assert cleaned == "Anchored the subject and pruned the negative."
        assert contract == {
            "improved": [
                "subject anchored: \"lone hiker\" is now \"a lone hiker in a red parka\"",
                "negative pruned: 6 redundant terms removed",
            ],
            "questions": [
                {"text": "keep the rain ambience or push golden hour?", "options": ["rain", "golden hour"]},
                {"text": "anything to avoid in the background?", "options": []},
            ],
        }

    def test_only_improved_section(self):
        content = "Done.\n## improved\n- trimmed redundant adjectives\n"
        cleaned, contract = parse_reply_contract(content)

        assert cleaned == "Done."
        assert contract == {"improved": ["trimmed redundant adjectives"]}
        assert "questions" not in contract

    def test_only_questions_section(self):
        content = "Sure.\n## questions\n1. widescreen or square?\n"
        cleaned, contract = parse_reply_contract(content)

        assert cleaned == "Sure."
        assert contract == {"questions": [{"text": "widescreen or square?", "options": []}]}
        assert "improved" not in contract

    def test_no_sections_prose_only(self):
        content = "Just a plain reply with no structure at all."
        cleaned, contract = parse_reply_contract(content)

        assert cleaned == content
        assert contract is None

    def test_options_hint_parsed_and_stripped_from_text(self):
        content = "## questions\n1. rain or shine? [rain | shine | overcast]\n"
        _, contract = parse_reply_contract(content)

        question = contract["questions"][0]
        assert question["text"] == "rain or shine?"
        assert question["options"] == ["rain", "shine", "overcast"]

    def test_question_without_options_hint_has_empty_list(self):
        content = "## questions\n1. anything else to change?\n"
        _, contract = parse_reply_contract(content)

        assert contract["questions"][0]["options"] == []

    @pytest.mark.parametrize("marker", ["-", "*", "•"])
    def test_sloppy_bullet_markers_all_accepted(self, marker):
        content = f"## improved\n{marker} tightened the composition\n"
        _, contract = parse_reply_contract(content)

        assert contract["improved"] == ["tightened the composition"]

    @pytest.mark.parametrize("prefix", ["1.", "1)", "-", "*", "•"])
    def test_question_prefix_styles_all_accepted(self, prefix):
        content = f"## questions\n{prefix} keep the mood dark?\n"
        _, contract = parse_reply_contract(content)

        assert contract["questions"] == [{"text": "keep the mood dark?", "options": []}]

    def test_repeated_headers_concatenate_items(self):
        content = (
            "## improved\n"
            "- first change\n"
            "## improved\n"
            "- second change\n"
        )
        _, contract = parse_reply_contract(content)

        assert contract["improved"] == ["first change", "second change"]

    def test_header_case_insensitive_with_optional_colon(self):
        content = "## Improved:\n- a fix\n## QUESTIONS\n1. one more thing?\n"
        _, contract = parse_reply_contract(content)

        assert contract["improved"] == ["a fix"]
        assert contract["questions"][0]["text"] == "one more thing?"

    def test_header_only_garbage_falls_back_to_prose(self):
        """A header with no parseable items under it is not a real section —
        the whole reply is left as untouched prose rather than silently
        dropping the text that followed the header."""
        content = "## improved\njust a sentence, not a bullet at all\n"
        cleaned, contract = parse_reply_contract(content)

        assert cleaned == content
        assert contract is None

    def test_empty_content(self):
        cleaned, contract = parse_reply_contract("")
        assert cleaned == ""
        assert contract is None

    def test_never_raises_on_weird_input(self):
        parse_reply_contract("## improved\n" * 50 + "[[[" * 20)


class TestToolActionTagSurvivesSectionParsing:
    """A `<tool_action>` version tag is the Apply-block deliverable (see
    src.features.llm.tools.builtin.form_context_tool) -- it must reach
    `cleaned` no matter where in the reply the model places it. Before this
    fix, a tag placed after a `## improved`/`## questions` header was silently
    dropped: the header-truncation only kept ``lines[:first_header_idx]``, and
    the line-based section parser only ever collected recognized bullets/
    questions, discarding anything else post-header -- exactly reproducing
    the reported bug (assistant says "Done." with a bullet summary and no
    Apply block, even though it wrote the tag)."""

    def test_tag_after_improved_header_is_preserved(self):
        content = (
            'Done.\n'
            '## improved\n'
            '- warm palette: cool blues swapped for warm oranges\n'
            '<tool_action type="update_segment" segment_index="0" segment_id="seg-1">'
            'a lone hiker in warm golden light</tool_action>\n'
        )
        cleaned, contract = parse_reply_contract(content)

        assert '<tool_action type="update_segment"' in cleaned
        assert 'a lone hiker in warm golden light' in cleaned
        assert contract == {"improved": ["warm palette: cool blues swapped for warm oranges"]}

    def test_tag_after_questions_header_is_preserved(self):
        content = (
            'Done.\n'
            '## questions\n'
            '1. keep the mood dark?\n'
            '<tool_action type="update_segment" segment_index="0" segment_id="seg-1">'
            'a darker variant</tool_action>\n'
        )
        cleaned, contract = parse_reply_contract(content)

        assert '<tool_action type="update_segment"' in cleaned
        assert contract == {"questions": [{"text": "keep the mood dark?", "options": []}]}

    def test_tag_before_header_still_preserved(self):
        """The already-working placement (tag in the lead line) must not regress."""
        content = (
            'Done. <tool_action type="update_segment" segment_index="0" segment_id="seg-1">'
            'a lone hiker in warm golden light</tool_action>\n'
            '## improved\n'
            '- warm palette: cool blues swapped for warm oranges\n'
        )
        cleaned, contract = parse_reply_contract(content)

        assert '<tool_action type="update_segment"' in cleaned
        assert contract == {"improved": ["warm palette: cool blues swapped for warm oranges"]}

    def test_multiline_tag_content_not_misread_as_bullets(self):
        """Proposed prompt text inside the tag can itself start with `-` --
        it must not be mistaken for an `## improved` bullet and eaten."""
        content = (
            'Done.\n'
            '## improved\n'
            '- pruned redundant terms\n'
            '<tool_action type="update_segment" segment_index="0" segment_id="seg-1">'
            'a lone hiker\n- in a red parka\n- at dusk</tool_action>\n'
        )
        cleaned, contract = parse_reply_contract(content)

        assert '- in a red parka' in cleaned
        assert '- at dusk' in cleaned
        assert contract == {"improved": ["pruned redundant terms"]}

    def test_no_tag_present_behaves_as_before(self):
        content = "Done.\n## improved\n- trimmed redundant adjectives\n"
        cleaned, contract = parse_reply_contract(content)

        assert cleaned == "Done."
        assert contract == {"improved": ["trimmed redundant adjectives"]}

    def test_tag_inline_on_a_bullet_line_does_not_leak_into_reply_contract(self):
        """`_BULLET_RE` captures the whole remainder of the line, so a tag
        written inline on the bullet (`- warm palette <tool_action ...>`) must
        not leave the raw placeholder token in the structured `improved` list
        -- and the tag it stood for must still reach `cleaned`, not vanish."""
        content = (
            'Done.\n'
            '## improved\n'
            '- warm palette <tool_action type="update_segment" segment_index="0" '
            'segment_id="seg-1">a lone hiker in warm golden light</tool_action>\n'
        )
        cleaned, contract = parse_reply_contract(content)

        assert '\x00' not in cleaned
        assert '<tool_action type="update_segment"' in cleaned
        assert 'a lone hiker in warm golden light' in cleaned
        assert contract == {"improved": ["warm palette"]}
        assert '\x00' not in contract["improved"][0]

    def test_tag_inline_on_a_question_line_does_not_leak(self):
        content = (
            'Done.\n'
            '## questions\n'
            '1. keep this variant? <tool_action type="update_segment" segment_index="0" '
            'segment_id="seg-1">a darker variant</tool_action>\n'
        )
        cleaned, contract = parse_reply_contract(content)

        assert '\x00' not in cleaned
        assert '<tool_action type="update_segment"' in cleaned
        assert contract == {"questions": [{"text": "keep this variant?", "options": []}]}
