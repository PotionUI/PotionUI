"""Tests for src.features.llm.context_budget — model-aware request budgeting."""

from types import SimpleNamespace

import pytest

from src.features.llm import context_budget
from src.features.llm.context_budget import (
    CapacityInfo,
    ContextBudgetExceededError,
    count_messages,
    count_text,
    count_tool_schemas,
    enforce_budget,
    fit_messages,
    multimodal_allowance,
    resolve_capacity,
)


def _config(**overrides):
    defaults = dict(type="openai", provider_options=None, max_tokens=1000)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# resolve_capacity
# ---------------------------------------------------------------------------

class TestResolveCapacity:
    def test_unconfigured_config_is_unknown_with_conservative_default(self):
        capacity = resolve_capacity(_config())
        assert capacity.source == "unknown"
        assert capacity.capacity_tokens == context_budget.UNKNOWN_CAPACITY_TOKENS

    def test_generic_context_window_wins_for_any_provider_type(self):
        capacity = resolve_capacity(_config(type="openai", provider_options={"context_window": 32000}))
        assert capacity == CapacityInfo(32000, "config")

    def test_ollama_num_ctx_is_honoured_as_the_existing_operator_override(self):
        capacity = resolve_capacity(_config(type="ollama", provider_options={"num_ctx": 4096}))
        assert capacity == CapacityInfo(4096, "config")

    def test_generic_context_window_takes_priority_over_num_ctx(self):
        capacity = resolve_capacity(
            _config(type="ollama", provider_options={"num_ctx": 4096, "context_window": 16000})
        )
        assert capacity == CapacityInfo(16000, "config")

    def test_num_ctx_is_ignored_for_non_ollama_types(self):
        capacity = resolve_capacity(_config(type="openai", provider_options={"num_ctx": 4096}))
        assert capacity.source == "unknown"

    @pytest.mark.parametrize("bad_value", [0, -10, "4096", None, True, False])
    def test_non_positive_or_non_numeric_context_window_falls_back_to_unknown(self, bad_value):
        capacity = resolve_capacity(_config(provider_options={"context_window": bad_value}))
        assert capacity.source == "unknown"

    def test_never_infers_capacity_from_model_name(self):
        # A model name that looks like it encodes a window size must never be
        # parsed for capacity — only explicit configuration counts.
        capacity = resolve_capacity(_config(model="llama-3-8k-instruct"))
        assert capacity.source == "unknown"


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

class TestCountText:
    def test_no_counter_uses_labelled_chars_per_token_estimate(self):
        result = count_text("a" * 70, None)
        assert result.measured is False
        assert result.tokens == 20  # ceil(70 / 3.5)

    def test_empty_text_is_zero_tokens(self):
        assert count_text("", None) == context_budget.TokenCount(0, False)
        assert count_text(None, None) == context_budget.TokenCount(0, False)

    def test_counter_is_used_and_marked_measured(self):
        result = count_text("hello world", lambda t: 3)
        assert result == context_budget.TokenCount(3, True)

    def test_counter_exception_falls_back_to_estimate(self):
        def broken_counter(_text):
            raise RuntimeError("tokenizer unavailable")

        result = count_text("a" * 35, broken_counter)
        assert result.measured is False
        assert result.tokens == 10


class TestCountMessages:
    def test_sums_content_plus_a_framing_allowance_per_message(self):
        messages = [{"role": "user", "content": "a" * 7}, {"role": "assistant", "content": "a" * 7}]
        result = count_messages(messages, lambda t: len(t))
        expected = 14 + 2 * context_budget.FRAMING_TOKENS_PER_MESSAGE
        assert result == context_budget.TokenCount(expected, False)

    def test_counts_serialized_tool_calls_as_real_payload(self):
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "get_thing", "arguments": {"id": 1}}}],
        }]
        with_calls = count_messages(messages, None).tokens
        without_calls = count_messages([{"role": "assistant", "content": ""}], None).tokens
        assert with_calls > without_calls

    def test_never_measured_even_with_a_real_per_fragment_tokenizer(self):
        """The framing allowance is always an estimate layered on top, so a
        per-fragment sum can never claim `measured=True` on its own — only a
        whole-request chat-template count can (see TestEnforceBudget)."""
        result = count_messages([{"role": "user", "content": "hi"}], lambda t: 1)
        assert result.measured is False

    def test_empty_list_is_zero_and_unmeasured(self):
        assert count_messages([], None) == context_budget.TokenCount(0, False)


class TestCountToolSchemas:
    def test_empty_or_none_is_zero_and_measured(self):
        assert count_tool_schemas(None, None) == context_budget.TokenCount(0, True)
        assert count_tool_schemas([], None) == context_budget.TokenCount(0, True)

    def test_serializes_the_whole_schema_set(self):
        schemas = [{"function": {"name": "a", "description": "x" * 20}}]
        result = count_tool_schemas(schemas, lambda t: len(t))
        assert result.tokens == len(context_budget.json.dumps(schemas))


class TestMultimodalAllowance:
    def test_no_image_is_zero(self):
        assert multimodal_allowance(None) == 0
        assert multimodal_allowance("") == 0

    def test_default_conservative_constant_when_attached(self):
        assert multimodal_allowance("base64...") == context_budget.DEFAULT_IMAGE_TOKEN_ESTIMATE

    def test_provider_known_cost_overrides_the_default(self):
        assert multimodal_allowance("base64...", per_image_tokens=42) == 42


# ---------------------------------------------------------------------------
# fit_messages — trimming
# ---------------------------------------------------------------------------

def _msg(role, content, **extra):
    return {"role": role, "content": content, **extra}


class TestFitMessages:
    def test_everything_fits_when_under_budget(self):
        messages = [_msg("user", "a" * 10), _msg("assistant", "a" * 10), _msg("user", "a" * 10)]
        result = fit_messages(messages, available_tokens=10_000, counter=None)
        assert result.messages == messages
        assert result.fits is True
        assert result.dropped_messages == 0

    def test_drops_oldest_first(self):
        messages = [_msg("user", "old " + "a" * 100), _msg("assistant", "mid " + "a" * 100), _msg("user", "new")]
        # Budget for only the last unit or two (character counter for determinism;
        # the exact cutoff shifts with FRAMING_TOKENS_PER_MESSAGE, so this only
        # asserts oldest-first ordering, not a specific dropped count).
        result = fit_messages(messages, available_tokens=110, counter=len)
        assert result.messages[-1]["content"] == "new"
        assert not any("old" in m["content"] for m in result.messages)
        assert result.dropped_messages >= 1

    def test_never_splits_a_tool_call_result_group(self):
        assistant_call = _msg("assistant", "", tool_calls=[{"function": {"name": "f", "arguments": {}}}])
        tool_result = {"role": "tool", "content": "result", "tool_call_id": "1"}
        messages = [
            _msg("user", "old " + "a" * 200),
            assistant_call,
            tool_result,
            _msg("user", "current"),
        ]
        # Budget only large enough for the protected tail plus the tool group,
        # never for the old message — the group must come through whole or
        # not at all, never split.
        result = fit_messages(messages, available_tokens=60, counter=len)
        roles = [m["role"] for m in result.messages]
        if "tool" in roles:
            assert "assistant" in roles and roles.index("assistant") < roles.index("tool")
        else:
            assert "tool" not in roles

    def test_protects_the_last_unit_even_if_it_alone_exceeds_budget(self):
        messages = [_msg("user", "a" * 500)]
        result = fit_messages(messages, available_tokens=1, counter=len)
        assert result.messages == messages
        assert result.fits is False

    def test_protects_trailing_system_blocks_stacked_before_the_user_message(self):
        # Mirrors ChatContextBuilder's injection order: several single system
        # messages immediately followed by the current user turn.
        messages = [
            _msg("user", "ancient " + "a" * 300),
            _msg("system", "memory block"),
            _msg("system", "contributor block"),
            _msg("user", "current question"),
        ]
        result = fit_messages(messages, available_tokens=1, counter=len)
        assert [m["content"] for m in result.messages] == [
            "memory block", "contributor block", "current question",
        ]

    def test_empty_messages_fits_trivially(self):
        result = fit_messages([], available_tokens=0, counter=None)
        assert result == context_budget.TrimResult(
            [], 0, 0, True, 0, True, kept_units=[], protected_count=0,
        )


# ---------------------------------------------------------------------------
# enforce_budget — the orchestration every gateway call runs through
# ---------------------------------------------------------------------------

class TestEnforceBudget:
    def test_fits_returns_trimmed_messages_and_a_ledger(self):
        outcome = enforce_budget(
            capacity_tokens=1000,
            capacity_source="config",
            reserve_tokens=100,
            system_message="You are helpful.",
            messages=[_msg("user", "hello")],
            tool_schemas=None,
            image_data=None,
            counter=None,
        )
        assert outcome.messages == [_msg("user", "hello")]
        assert outcome.ledger["capacity_tokens"] == 1000
        assert outcome.ledger["capacity_source"] == "config"
        assert outcome.ledger["reserve_tokens"] == 100
        assert outcome.ledger["measured"] is False

    def test_raises_when_irreducibly_over_budget(self):
        with pytest.raises(ContextBudgetExceededError) as exc_info:
            enforce_budget(
                capacity_tokens=10,
                capacity_source="config",
                reserve_tokens=5,
                system_message=None,
                messages=[_msg("user", "a" * 200)],
                counter=len,
            )
        err = exc_info.value
        assert err.capacity_tokens == 10
        assert err.reserve_tokens == 5
        assert err.available_tokens == 5
        assert err.over_by_tokens > 0
        assert "over by" in str(err)

    def test_system_and_tool_schema_cost_reduces_room_for_history(self):
        big_system = "s" * 700  # ~200 tokens at 3.5 chars/token
        outcome = enforce_budget(
            capacity_tokens=300,
            capacity_source="config",
            reserve_tokens=0,
            system_message=big_system,
            messages=[_msg("user", "a" * 50)],
            counter=None,
        )
        assert outcome.ledger["system_tokens"] > 0
        assert outcome.messages == [_msg("user", "a" * 50)]

    def test_no_counter_is_the_estimate_tier(self):
        outcome = enforce_budget(
            capacity_tokens=10_000, capacity_source="config", reserve_tokens=0,
            system_message="sys", messages=[_msg("user", "hi")],
        )
        assert outcome.ledger["accounting"] == "estimate"
        assert outcome.ledger["measured"] is False

    def test_a_real_per_fragment_counter_alone_is_fragments_plus_framing_never_measured(self):
        outcome = enforce_budget(
            capacity_tokens=10_000,
            capacity_source="config",
            reserve_tokens=0,
            system_message="sys",
            messages=[_msg("user", "hi")],
            tool_schemas=[{"function": {"name": "f"}}],
            counter=len,
        )
        assert outcome.ledger["accounting"] == "fragments+framing"
        assert outcome.ledger["measured"] is False

    def test_a_working_messages_counter_is_the_chat_template_tier_and_is_measured(self):
        outcome = enforce_budget(
            capacity_tokens=10_000,
            capacity_source="config",
            reserve_tokens=0,
            system_message="sys",
            messages=[_msg("user", "hi")],
            counter=len,
            messages_counter=lambda system_message, messages: 42,
        )
        assert outcome.ledger["accounting"] == "chat_template"
        assert outcome.ledger["estimated_tokens"] == 42
        assert outcome.ledger["measured"] is True

    def test_a_raising_messages_counter_falls_back_to_the_fragment_decision(self):
        def broken(system_message, messages):
            raise RuntimeError("template not supported")

        outcome = enforce_budget(
            capacity_tokens=10_000, capacity_source="config", reserve_tokens=0,
            system_message="sys", messages=[_msg("user", "hi")],
            counter=len, messages_counter=broken,
        )
        assert outcome.ledger["accounting"] == "chat_template_fallback"
        assert outcome.ledger["measured"] is False
        assert outcome.messages == [_msg("user", "hi")]

    def test_image_attached_forces_measured_false_even_with_chat_template(self):
        outcome = enforce_budget(
            capacity_tokens=10_000, capacity_source="config", reserve_tokens=0,
            system_message="sys", messages=[_msg("user", "hi")],
            image_data="base64...", messages_counter=lambda s, m: 42,
        )
        assert outcome.ledger["accounting"] == "chat_template"
        assert outcome.ledger["measured"] is False

    def test_exact_recount_can_rescue_a_request_the_fragment_estimate_rejected(self):
        """Framing is deliberately conservative — an exact whole-request count
        must win even when the fragment/framing estimate alone said the
        protected tail didn't fit."""
        calls = []

        def generous_counter(system_message, messages):
            calls.append(len(messages))
            return 1  # the exact wire cost, however pessimistic the estimate was

        outcome = enforce_budget(
            capacity_tokens=10, capacity_source="config", reserve_tokens=5,
            system_message=None, messages=[_msg("user", "a" * 200)],
            counter=len, messages_counter=generous_counter,
        )
        assert calls == [1]  # consulted despite the fragment trim not fitting
        assert outcome.messages == [_msg("user", "a" * 200)]
        assert outcome.ledger["accounting"] == "chat_template"
        assert outcome.ledger["estimated_tokens"] == 1

    def test_recount_drops_one_more_unit_when_the_exact_count_still_overflows(self):
        """Fragment trimming decided to keep all three messages; the exact
        recount disagrees, so the oldest eligible unit is dropped and
        recounted again — bounded, one drop per failed recount."""
        messages = [
            _msg("user", "oldest"), _msg("assistant", "older"), _msg("user", "current"),
        ]
        # Exact cost scales with how many messages are in the candidate —
        # 3 kept -> 300 (over), 2 kept -> 200 (fits at capacity 250).
        outcome = enforce_budget(
            capacity_tokens=250, capacity_source="config", reserve_tokens=0,
            system_message=None, messages=messages, counter=len,
            messages_counter=lambda s, m: len(m) * 100,
        )
        assert [m["content"] for m in outcome.messages] == ["older", "current"]
        assert outcome.ledger["accounting"] == "chat_template_shrunk"
        assert outcome.ledger["chat_template_extra_dropped"] == 1
        assert outcome.ledger["estimated_tokens"] == 200
        assert outcome.ledger["measured"] is True

    def test_recount_raises_when_even_the_protected_unit_alone_overflows_exactly(self):
        """Shrinks down to the protected floor, recounts, still over — must
        raise using the EXACT numbers, not silently succeed."""
        messages = [
            _msg("user", "oldest"), _msg("assistant", "older"), _msg("user", "current"),
        ]
        with pytest.raises(ContextBudgetExceededError) as exc_info:
            enforce_budget(
                capacity_tokens=50, capacity_source="config", reserve_tokens=0,
                system_message=None, messages=messages, counter=len,
                messages_counter=lambda s, m: len(m) * 100,
            )
        err = exc_info.value
        assert err.estimated_tokens == 100  # the protected unit alone, exactly
        assert err.breakdown["accounting"] == "chat_template_shrunk"
        assert err.breakdown["chat_template_extra_dropped"] == 2

    def test_a_counter_that_raises_mid_shrink_falls_back_to_the_original_fragment_selection(self):
        """The recount loop drops one unit, then the counter starts failing —
        the whole exact-recount attempt is abandoned, reverting to whatever
        the fragment-based trim originally decided (all three messages, in
        this fixture), not the partially-shrunk candidate."""
        messages = [
            _msg("user", "oldest"), _msg("assistant", "older"), _msg("user", "current"),
        ]

        def flaky(system_message, kept_messages):
            if len(kept_messages) == 3:
                return 300  # over budget -> triggers one drop
            raise RuntimeError("template blew up on the shrunk candidate")

        outcome = enforce_budget(
            capacity_tokens=250, capacity_source="config", reserve_tokens=0,
            system_message=None, messages=messages, counter=len,
            messages_counter=flaky,
        )
        assert [m["content"] for m in outcome.messages] == ["oldest", "older", "current"]
        assert outcome.ledger["accounting"] == "chat_template_fallback"
        assert outcome.ledger["chat_template_extra_dropped"] == 0
        assert outcome.ledger["measured"] is False

    def test_image_attached_adds_the_multimodal_allowance(self):
        without_image = enforce_budget(
            capacity_tokens=10_000, capacity_source="config", reserve_tokens=0,
            system_message="", messages=[_msg("user", "hi")], image_data=None,
        )
        with_image = enforce_budget(
            capacity_tokens=10_000, capacity_source="config", reserve_tokens=0,
            system_message="", messages=[_msg("user", "hi")], image_data="base64...",
        )
        assert with_image.ledger["image_tokens"] == context_budget.DEFAULT_IMAGE_TOKEN_ESTIMATE
        assert without_image.ledger["image_tokens"] == 0
        assert with_image.ledger["estimated_tokens"] > without_image.ledger["estimated_tokens"]

    def test_dropped_counts_reported_in_the_ledger(self):
        messages = [_msg("user", "old " + "a" * 400), _msg("user", "new")]
        outcome = enforce_budget(
            capacity_tokens=50, capacity_source="config", reserve_tokens=0,
            system_message="", messages=messages, counter=len,
        )
        assert outcome.ledger["messages_dropped"] == 1
        assert outcome.ledger["groups_dropped"] == 1
        assert outcome.ledger["messages_sent"] == 1
        assert outcome.ledger["messages_total"] == 2


# ---------------------------------------------------------------------------
# resolve_image_token_override
# ---------------------------------------------------------------------------

class TestResolveImageTokenOverride:
    def test_unset_is_none(self):
        assert context_budget.resolve_image_token_override(_config()) is None

    def test_explicit_override_is_honoured(self):
        config = _config(provider_options={"image_token_estimate": 250})
        assert context_budget.resolve_image_token_override(config) == 250

    @pytest.mark.parametrize("bad_value", [0, -5, "250", None, True])
    def test_non_positive_or_non_numeric_is_none(self, bad_value):
        config = _config(provider_options={"image_token_estimate": bad_value})
        assert context_budget.resolve_image_token_override(config) is None

    def test_threaded_through_enforce_budget_as_the_per_image_cost(self):
        outcome = enforce_budget(
            capacity_tokens=10_000, capacity_source="config", reserve_tokens=0,
            system_message="", messages=[_msg("user", "hi")],
            image_data="base64...", image_tokens=context_budget.resolve_image_token_override(
                _config(provider_options={"image_token_estimate": 77})
            ),
        )
        assert outcome.ledger["image_tokens"] == 77


# ---------------------------------------------------------------------------
# Current-turn protection against realistic ToolWorkflow-built message lists
# ---------------------------------------------------------------------------

def _workflow(messages, iteration_nudge=None, wrap_up_on_limit=True):
    from unittest.mock import Mock

    from src.features.llm.tools.workflow import ToolWorkflow

    return ToolWorkflow(
        executor=Mock(),
        messages=messages,
        tool_context=Mock(),
        allowed_tools=None,
        max_iterations=5,
        iteration_nudge=iteration_nudge,
        wrap_up_on_limit=wrap_up_on_limit,
    )


class TestCurrentTurnProtectionAcrossWorkflowCalls:
    """The current turn — its injected context blocks, its tool rounds, and
    any trailing nudge the workflow appends — must survive trimming at every
    stage of a tool loop, built through the SAME `ToolWorkflow` helpers the
    real send paths use so the message shape can't drift from what this
    test exercises.
    """

    OLD_HISTORY = [
        {"role": "user", "content": "an old unrelated question " + "x" * 300},
        {"role": "assistant", "content": "an old unrelated answer " + "x" * 300},
    ]
    CONTEXT_BLOCKS = [
        {"role": "system", "content": "recalled memory: the user prefers dark mode"},
        {"role": "system", "content": "contributor: active preset is SDXL-Anime"},
    ]
    QUESTION = {"role": "user", "content": "What resolution does the Anime preset render at?"}
    TOOL_CALL = {
        "role": "assistant", "content": "",
        "tool_calls": [{"function": {"name": "get_model_info", "arguments": {}}}],
    }
    TOOL_RESULT = {"role": "tool", "content": "resolution: 896x1152", "tool_call_id": "1"}

    # Small enough that the old history is clearly dropped, large enough
    # that the current turn's own stack (question + context + one tool
    # round + a nudge) fits.
    BUDGET_TOKENS = 400

    def _base_messages(self):
        return list(self.OLD_HISTORY) + list(self.CONTEXT_BLOCKS) + [dict(self.QUESTION)]

    def _assert_current_turn_survived(self, sent):
        contents = [m.get("content") or "" for m in sent]
        assert not any("old unrelated" in c for c in contents)
        assert any("resolution does the Anime preset" in c for c in contents)
        assert any("dark mode" in c for c in contents)
        assert any("SDXL-Anime" in c for c in contents)

    def test_first_call(self):
        """The turn's very first request — no tool round has happened yet."""
        messages = self._base_messages()
        outcome = enforce_budget(
            capacity_tokens=self.BUDGET_TOKENS, capacity_source="config", reserve_tokens=0,
            system_message=None, messages=messages, counter=len,
        )
        self._assert_current_turn_survived(outcome.messages)
        assert outcome.messages[-1] == self.QUESTION

    def test_next_call_with_the_iteration_nudge(self):
        """A tool round has completed; the workflow's own `_next_request`
        appends the iteration nudge exactly as it would for a live turn —
        built through the real method, not a hand-rolled equivalent."""
        nudge = "Reminder: call a tool or answer now."
        workflow = _workflow(self._base_messages(), iteration_nudge=nudge)
        workflow.working_messages.append(dict(self.TOOL_CALL))
        workflow.working_messages.append(dict(self.TOOL_RESULT))
        workflow._any_tool_round_completed = True

        request = workflow._next_request()

        outcome = enforce_budget(
            capacity_tokens=self.BUDGET_TOKENS, capacity_source="config", reserve_tokens=0,
            system_message=None, messages=request.messages, counter=len,
        )
        self._assert_current_turn_survived(outcome.messages)
        sent_roles = [m["role"] for m in outcome.messages]
        assert "tool" in sent_roles  # the completed round's result must survive
        assert outcome.messages[-1] == {"role": "system", "content": nudge}

    def test_final_call_with_the_budget_exhausted_message(self):
        """The tool-iteration budget ran out; `_final_request` appends the
        real `TOOL_BUDGET_EXHAUSTED_MESSAGE`, built the same way the live
        wrap-up call does."""
        workflow = _workflow(self._base_messages(), wrap_up_on_limit=True)
        workflow.working_messages.append(dict(self.TOOL_CALL))
        workflow.working_messages.append(dict(self.TOOL_RESULT))

        request = workflow._final_request()

        outcome = enforce_budget(
            capacity_tokens=self.BUDGET_TOKENS, capacity_source="config", reserve_tokens=0,
            system_message=None, messages=request.messages, counter=len,
        )
        self._assert_current_turn_survived(outcome.messages)
        assert outcome.messages[-1] == {
            "role": "system", "content": workflow.TOOL_BUDGET_EXHAUSTED_MESSAGE,
        }

    def test_irreducible_current_turn_raises_rather_than_trimming_into_it(self):
        """When the current turn's own stack alone doesn't fit, the request
        must be refused, never silently trimmed into the question, its
        context blocks, or its tool round."""
        workflow = _workflow(self._base_messages(), iteration_nudge="go on")
        workflow.working_messages.append(dict(self.TOOL_CALL))
        workflow.working_messages.append(dict(self.TOOL_RESULT))
        workflow._any_tool_round_completed = True
        request = workflow._next_request()

        with pytest.raises(ContextBudgetExceededError):
            enforce_budget(
                capacity_tokens=20, capacity_source="config", reserve_tokens=5,
                system_message=None, messages=request.messages, counter=len,
            )
