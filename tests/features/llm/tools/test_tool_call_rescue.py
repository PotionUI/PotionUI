"""Tests for near-miss tool-call detection and repair helpers."""

from src.features.llm.tools import tool_call_rescue as rescue

REGISTERED = {"update_video_director", "echo"}


class TestFindNearMiss:
    def test_tool_action_tag_with_json_attribute(self):
        content = (
            '<tool_action type="update_video_director" '
            "operations='[{\"op\": \"set_mode\", \"mode\": \"i2v\"}]'>"
        )
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        assert len(near) == 1
        nm = near[0]
        assert nm.tool_name == "update_video_director"
        assert nm.original_format == "tool_action_tag"
        assert nm.arguments == {"operations": [{"op": "set_mode", "mode": "i2v"}]}

    def test_truncated_tool_action_tag_still_detected(self):
        # A cut-off tag (no closing '>') is still a near-miss; its JSON won't
        # parse, so the payload stays text and fails the schema's array type
        # (ambiguous → the executor retries with the reason).
        content = '<tool_action type="update_video_director" operations=\'[{"op": "set_mode"'
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        assert len(near) == 1
        assert near[0].tool_name == "update_video_director"
        assert rescue.validate_arguments(near[0].arguments, UPDATE_SCHEMA) is False

    def test_segment_tool_action_tags_are_not_a_near_miss(self):
        # update_segment and update_director_segment are both frontend-only
        # conventions, never registered tools -- neither is flagged even
        # though update_video_director (a similarly-named real tool) IS
        # registered.
        content = (
            '<tool_action type="update_segment" segment_index="0" '
            'segment_id="seg-1">a lone hiker in a red parka</tool_action>'
            '<tool_action type="update_director_segment" segment_index="0" '
            'segment_id="a">new shot text</tool_action>'
        )
        assert rescue.find_near_miss_invocations(content, REGISTERED) == []

    def test_tool_call_fence(self):
        content = '```tool_call\n{"name": "echo", "arguments": {"message": "hi"}}\n```'
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        assert len(near) == 1
        assert near[0].tool_name == "echo"
        assert near[0].original_format == "code_fence"
        assert near[0].arguments == {"message": "hi"}

    def test_bare_json_with_name_and_arguments(self):
        content = 'Sure: {"name": "echo", "arguments": {"message": "hi"}}'
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        assert len(near) == 1
        assert near[0].tool_name == "echo"
        assert near[0].original_format == "bare_json"

    def test_bare_json_without_args_key_is_ignored(self):
        # A bare object naming a tool but carrying no arguments key is not
        # treated as an invocation — too close to ordinary JSON.
        content = 'The tool is {"name": "echo"} in the registry.'
        assert rescue.find_near_miss_invocations(content, REGISTERED) == []

    def test_plain_prose_naming_a_tool_is_not_rescued(self):
        content = "I already called echo and get_form_state to check your setup."
        assert rescue.find_near_miss_invocations(content, REGISTERED) == []

    def test_user_requested_code_block_is_not_rescued(self):
        content = '```python\nresult = call("update_video_director")\n```'
        assert rescue.find_near_miss_invocations(content, REGISTERED) == []

    def test_empty_or_no_registered_names(self):
        assert rescue.find_near_miss_invocations("", REGISTERED) == []
        assert rescue.find_near_miss_invocations('<tool_action type="echo">', set()) == []

    def test_closed_tool_call_with_unparseable_json_is_steered_not_dropped(self):
        # A literal control character inside a string value (a raw newline
        # instead of \n) breaks json.loads even though the tags are complete.
        content = '<tool_call>{"name": "echo", "arguments": {"message": "hi\nthere"}}</tool_call>'
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        assert len(near) == 1
        assert near[0].tool_name == "echo"
        assert near[0].original_format == "tool_call_malformed_json"
        assert near[0].arguments is None
        assert "did not parse" in near[0].problem

    def test_closed_tool_call_naming_an_unregistered_tool_is_steered(self):
        content = '<tool_call>{"name": "not_a_real_tool", "arguments": {}}</tool_call>'
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        assert len(near) == 1
        assert near[0].tool_name == "not_a_real_tool"
        assert "not one of this session's available tools" in near[0].problem

    def test_closed_tool_call_with_parameters_key_is_repaired(self):
        # "parameters" (the schema's own field name) instead of "arguments" —
        # a complete, valid payload, so this is a real repair, not a steer.
        content = '<tool_call>{"name": "echo", "parameters": {"message": "hi"}}</tool_call>'
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        assert len(near) == 1
        assert near[0].tool_name == "echo"
        assert near[0].arguments == {"message": "hi"}

    def test_closed_tool_call_with_mangled_quotes_is_repaired(self):
        content = (
            '<tool_call>{<|"|>name<|"|>: <|"|>echo<|"|>, <|"|>arguments<|"|>: '
            '{<|"|>message<|"|>: <|"|>hi<|"|>}}</tool_call>'
        )
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        assert len(near) == 1
        assert near[0].tool_name == "echo"
        assert near[0].arguments == {"message": "hi"}

    def test_valid_closed_tool_call_is_not_flagged(self):
        # A fully valid call is the primary parser's job, not rescue's — it
        # never reaches find_near_miss_invocations in the real flow, but the
        # scanner itself must still repair rather than ignore it if it does.
        content = '<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>'
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        assert len(near) == 1
        assert near[0].arguments == {"message": "hi"}


class TestFindTruncatedToolCall:
    def test_unclosed_tool_call_is_truncated(self):
        content = (
            '<tool_call>{"name": "update_video_director", "arguments": {"operations": '
            '[{"op": "upsert_segment", "segment": {"pr'
        )
        truncated = rescue.find_truncated_tool_call(content)
        assert truncated is not None
        assert truncated.tool_name == "update_video_director"
        assert truncated.span == (0, len(content))

    def test_cut_before_name_field_has_no_tool_name(self):
        content = 'Sure, one moment. <tool_call>{"na'
        truncated = rescue.find_truncated_tool_call(content)
        assert truncated is not None
        assert truncated.tool_name is None

    def test_closed_tool_call_is_not_truncated(self):
        content = '<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>'
        assert rescue.find_truncated_tool_call(content) is None

    def test_closed_call_followed_by_truncated_one_flags_only_the_second(self):
        content = (
            '<tool_call>{"name": "echo", "arguments": {"message": "hi"}}</tool_call>'
            ' and then <tool_call>{"name": "update_video_director", "arg'
        )
        truncated = rescue.find_truncated_tool_call(content)
        assert truncated is not None
        assert truncated.tool_name == "update_video_director"

    def test_empty_content(self):
        assert rescue.find_truncated_tool_call("") is None
        assert rescue.find_truncated_tool_call(None) is None

    def test_plain_prose_without_a_tool_call_tag(self):
        assert rescue.find_truncated_tool_call("just a normal answer") is None


class TestTruncatedNudgeAndFallback:
    def test_nudge_names_the_tool_and_shows_the_full_call_example(self):
        nudge = rescue.truncated_retry_nudge("update_video_director")
        assert "update_video_director" in nudge
        assert "cut off" in nudge
        assert '<tool_call>{"name": "get_form_state", "arguments": {}}</tool_call>' in nudge

    def test_nudge_without_a_tool_name_is_still_generic_and_complete(self):
        nudge = rescue.truncated_retry_nudge(None)
        assert "your tool call" in nudge
        assert "<tool_call>" in nudge

    def test_fallback_names_the_tool_and_never_shows_markup(self):
        fallback = rescue.truncated_fallback_message("update_video_director")
        assert "update_video_director" in fallback
        assert "<tool_call>" not in fallback


class TestValidateArguments:
    def test_all_required_present(self):
        schema = {"required": ["operations"]}
        assert rescue.validate_arguments({"operations": []}, schema) is True

    def test_missing_required(self):
        schema = {"required": ["operations"]}
        assert rescue.validate_arguments({}, schema) is False

    def test_no_required_keys(self):
        assert rescue.validate_arguments({"anything": 1}, {"type": "object"}) is True

    def test_non_dict_arguments(self):
        assert rescue.validate_arguments(None, {"required": []}) is False
        assert rescue.validate_arguments([1, 2], {"required": []}) is False


class TestStripSpans:
    def test_removes_spans_and_collapses_whitespace(self):
        content = "before\n\n<tool_action type=\"echo\">\n\nafter"
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        cleaned = rescue.strip_spans(content, [nm.span for nm in near])
        assert "tool_action" not in cleaned
        assert "before" in cleaned and "after" in cleaned

    def test_no_spans_returns_content(self):
        assert rescue.strip_spans("hello", []) == "hello"


class TestNudgeAndFallback:
    def test_retry_nudge_quotes_format_and_tool_name(self):
        nudge = rescue.retry_nudge(["update_video_director"])
        assert "update_video_director" in nudge
        assert "<tool_call>" in nudge
        assert "<tool_action>" in nudge  # tells the model what NOT to do

    def test_fallback_message_is_honest_and_has_no_markup(self):
        msg = rescue.fallback_message(["update_video_director"])
        assert "update_video_director" in msg
        assert "couldn't format" in msg
        assert "<tool_action" not in msg


# The verbatim shape a local model produced instead of a tool
# call: a pseudo-XML tag, a Python-repr payload, AND an invalid dict literal
# (two colons in one entry).
MALFORMED_LOCAL_MODEL_CALL = (
    "<tool_action type=\"update_video_director\" operations=\"[{'op': 'set_prompt': "
    "'integrated_multimodal_description: [Shot 1] Live-action, cinematic, ...\">"
)

UPDATE_SCHEMA = {
    "type": "object",
    "properties": {"operations": {"type": "array"}},
    "required": ["operations"],
}


class TestDecodePayload:
    def test_json_payload(self):
        assert rescue.decode_payload('[{"op": "set_mode"}]') == ([{"op": "set_mode"}], None)

    def test_python_repr_payload_is_recovered(self):
        # Models trained on Python emit single-quoted reprs; ast.literal_eval
        # reads them, eval is never used.
        value, problem = rescue.decode_payload("[{'op': 'set_mode', 'mode': 'i2v'}]")
        assert value == [{"op": "set_mode", "mode": "i2v"}]
        assert problem is None

    def test_python_repr_object_payload(self):
        assert rescue.decode_payload("{'a': 1}") == ({"a": 1}, None)

    def test_bare_word_stays_a_string_and_is_not_a_problem(self):
        assert rescue.decode_payload("update_video_director") == ("update_video_director", None)

    def test_bare_word_is_never_literal_evaled_into_a_python_value(self):
        assert rescue.decode_payload("None") == ("None", None)
        assert rescue.decode_payload("(1, 2)") == ("(1, 2)", None)

    def test_unparseable_container_reports_the_fix(self):
        value, problem = rescue.decode_payload("[{'op': 'set_prompt': 'text'}]")
        assert value == "[{'op': 'set_prompt': 'text'}]"
        assert "two colons" in problem
        assert '{"op": "set_prompt", "prompt": "text"}' in problem

    def test_truncated_container_says_so(self):
        value, problem = rescue.decode_payload('[{"op": "set_mode"')
        assert "cut off" in problem


class TestValidateArgumentsTypes:
    def test_required_array_given_as_text_is_rejected(self):
        # The check that keeps an unparsed payload from being dispatched as a
        # string the tool would iterate character by character.
        assert rescue.validate_arguments({"operations": "[{'op': 'x'}]"}, UPDATE_SCHEMA) is False

    def test_required_array_given_as_an_array_passes(self):
        assert rescue.validate_arguments({"operations": [{"op": "x"}]}, UPDATE_SCHEMA) is True

    def test_undeclared_type_is_not_checked(self):
        assert rescue.validate_arguments({"operations": "text"}, {"required": ["operations"]}) is True

    def test_boolean_is_not_a_number(self):
        schema = {"properties": {"n": {"type": "number"}}, "required": ["n"]}
        assert rescue.validate_arguments({"n": True}, schema) is False
        assert rescue.validate_arguments({"n": 1.5}, schema) is True


class TestMalformedLocalModelCall:
    def test_the_tag_is_detected_as_a_near_miss(self):
        near = rescue.find_near_miss_invocations(MALFORMED_LOCAL_MODEL_CALL, REGISTERED)
        assert len(near) == 1
        assert near[0].tool_name == "update_video_director"
        assert near[0].original_format == "tool_action_tag"

    def test_its_operations_payload_is_unrecoverable_and_says_why(self):
        nm = rescue.find_near_miss_invocations(MALFORMED_LOCAL_MODEL_CALL, REGISTERED)[0]
        # The payload survives as text (never silently dropped) but does not
        # satisfy the schema, so the executor steers instead of dispatching.
        assert isinstance(nm.arguments["operations"], str)
        assert rescue.validate_arguments(nm.arguments, UPDATE_SCHEMA) is False
        assert "'operations' could not be read" in nm.problem
        assert "two colons" in nm.problem

    def test_the_corrective_nudge_names_the_exact_fix(self):
        nm = rescue.find_near_miss_invocations(MALFORMED_LOCAL_MODEL_CALL, REGISTERED)[0]
        nudge = rescue.retry_nudge([nm.tool_name], [nm.problem])
        assert "<tool_call>" in nudge
        assert '{"op": "set_prompt", "prompt": "text"}' in nudge
        assert "double quotes" in nudge

    def test_nothing_of_the_raw_markup_reaches_the_user(self):
        content = f"Sure, updating that now.\n{MALFORMED_LOCAL_MODEL_CALL}"
        near = rescue.find_near_miss_invocations(content, REGISTERED)
        cleaned = rescue.strip_spans(content, [nm.span for nm in near])
        assert "tool_action" not in cleaned
        assert "set_prompt" not in cleaned

    def test_a_well_formed_python_repr_tag_is_rescued_outright(self):
        # Same wrong wrapper, valid payload: repaired rather than steered.
        content = (
            "<tool_action type=\"update_video_director\" "
            "operations=\"[{'op': 'upsert_segment', 'segment': {'prompt': 'a dune', 'duration': 4}}]\">"
        )
        nm = rescue.find_near_miss_invocations(content, REGISTERED)[0]
        assert nm.problem is None
        assert rescue.validate_arguments(nm.arguments, UPDATE_SCHEMA) is True
        assert nm.arguments["operations"][0]["segment"]["duration"] == 4


# A second verbatim sample from live testing: the semantic
# steering worked (per-shot upsert_segment ops with durations), but the
# transport arrived mangled three ways at once -- an UNQUOTED attribute value,
# quote characters wrapped in special-token delimiters, and unquoted object
# keys underneath.
TOKEN_MANGLED_CALL = (
    '<tool_action type="update_video_director" '
    'operations=[{op:<|"|>upsert_segment<|"|>,segment:{duration:3,prompt:<|"|>'
)
TOKEN_MANGLED_CALL_COMPLETE = TOKEN_MANGLED_CALL + 'a wide dune at dawn<|"|>}}]>'


class TestDemangleQuoteTokens:
    def test_double_and_single_quote_tokens_are_restored(self):
        assert rescue.demangle_quote_tokens('[{op:<|"|>x<|"|>}]') == '[{op:"x"}]'
        assert rescue.demangle_quote_tokens("[{op:<|'|>x<|'|>}]") == "[{op:'x'}]"

    def test_other_special_tokens_are_left_alone(self):
        # Real chat-template tokens must survive untouched.
        text = "<|im_start|>assistant<|eot_id|>"
        assert rescue.demangle_quote_tokens(text) == text

    def test_chat_prose_is_never_demangled(self):
        # The normalization is applied to a detected tool_action payload only,
        # so a message discussing the token keeps it verbatim.
        prose = 'Your model emits <|"|> instead of a quote.'
        assert rescue.find_near_miss_invocations(prose, REGISTERED) == []


class TestQuoteBareKeys:
    def test_bare_identifier_keys_are_quoted(self):
        assert rescue._quote_bare_keys('{op: "x", duration: 3}') == '{"op": "x", "duration": 3}'

    def test_keys_inside_string_values_are_left_alone(self):
        text = '{prompt: "cinematic: a wide dune"}'
        assert rescue._quote_bare_keys(text) == '{"prompt": "cinematic: a wide dune"}'

    def test_already_quoted_keys_are_untouched(self):
        text = '{"op": "x"}'
        assert rescue._quote_bare_keys(text) == text

    def test_decode_payload_reads_a_bare_key_object(self):
        value, problem = rescue.decode_payload('[{op:"upsert_segment",segment:{duration:3}}]')
        assert value == [{"op": "upsert_segment", "segment": {"duration": 3}}]
        assert problem is None


class TestUnquotedAttributeValues:
    def test_an_unquoted_bracket_payload_is_read_to_its_matching_close(self):
        content = '<tool_action type="update_video_director" operations=[{"op": "set_mode", "mode": "i2v"}]>'
        nm = rescue.find_near_miss_invocations(content, REGISTERED)[0]
        assert nm.arguments == {"operations": [{"op": "set_mode", "mode": "i2v"}]}

    def test_a_space_inside_the_payload_does_not_end_it(self):
        # The failure a naive "read to the next space" scan would produce.
        content = (
            '<tool_action type="update_video_director" '
            'operations=[{"op": "set_prompt", "prompt": "a wide dune at dawn"}]>'
        )
        nm = rescue.find_near_miss_invocations(content, REGISTERED)[0]
        assert nm.arguments["operations"][0]["prompt"] == "a wide dune at dawn"

    def test_a_bracket_inside_a_string_does_not_close_the_payload(self):
        content = (
            '<tool_action type="update_video_director" '
            'operations=[{"op": "set_prompt", "prompt": "shot [35mm] wide"}]>'
        )
        nm = rescue.find_near_miss_invocations(content, REGISTERED)[0]
        assert nm.arguments["operations"][0]["prompt"] == "shot [35mm] wide"

    def test_an_unbalanced_closer_inside_a_string_does_not_end_the_payload(self):
        # The property the balanced scan needs string-awareness for: counting
        # brackets blind, these two closers would pop the stack empty and cut
        # the payload off mid-value.
        text = '[{"prompt": "a }] b"}] tail'
        assert rescue._scan_balanced(text, 0) == '[{"prompt": "a }] b"}]'

    def test_a_quoted_attribute_still_works(self):
        content = '<tool_action type="echo" message="hi">'
        nm = rescue.find_near_miss_invocations(content, REGISTERED)[0]
        assert nm.arguments == {"message": "hi"}


class TestTokenMangledCall:
    def test_the_mangled_quotes_no_longer_truncate_the_tag(self):
        # The `>` inside `<|"|>` used to end the attribute region before
        # `operations` was ever seen.
        nm = rescue.find_near_miss_invocations(TOKEN_MANGLED_CALL, REGISTERED)[0]
        assert "operations" in (nm.arguments or {})

    def test_the_verbatim_truncated_sample_yields_a_corrective_error(self):
        nm = rescue.find_near_miss_invocations(TOKEN_MANGLED_CALL, REGISTERED)[0]
        assert rescue.validate_arguments(nm.arguments, UPDATE_SCHEMA) is False
        assert "cut off part-way" in nm.problem
        assert 'write {"op": "upsert_segment"}' in nm.problem

    def test_the_same_call_completed_is_rescued_end_to_end(self):
        nm = rescue.find_near_miss_invocations(TOKEN_MANGLED_CALL_COMPLETE, REGISTERED)[0]
        assert nm.problem is None
        assert rescue.validate_arguments(nm.arguments, UPDATE_SCHEMA) is True
        assert nm.arguments["operations"] == [
            {"op": "upsert_segment", "segment": {"duration": 3, "prompt": "a wide dune at dawn"}}
        ]

    def test_no_raw_markup_survives_either_way(self):
        for content in (TOKEN_MANGLED_CALL, TOKEN_MANGLED_CALL_COMPLETE):
            near = rescue.find_near_miss_invocations(content, REGISTERED)
            cleaned = rescue.strip_spans(content, [nm.span for nm in near])
            assert "tool_action" not in cleaned
            assert '<|"|>' not in cleaned


class TestPathologicalPayloads:
    """A malformed payload must never escape as an exception -- the rescue runs
    on every would-be-final assistant message, so anything raised here takes
    down the whole chat turn."""

    def test_deeply_nested_brackets_do_not_raise(self):
        # The stdlib decoders recurse; 3000 opening brackets exceed the
        # interpreter's limit and raise RecursionError, not ValueError.
        deep = "[" * 3000
        for content in (
            f'<tool_action type="update_video_director" operations={deep}>',
            f'<tool_action type="update_video_director" operations="{deep}">',
            f'{{"name": "update_video_director", "arguments": {deep}',
        ):
            assert isinstance(rescue.find_near_miss_invocations(content, REGISTERED), list)

    def test_deeply_nested_brackets_decode_to_a_problem_not_a_crash(self):
        value, problem = rescue.decode_payload("[" * 3000)
        assert value == "[" * 3000
        assert problem is not None
