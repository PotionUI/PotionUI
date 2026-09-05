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
    def test_sums_content_across_messages(self):
        messages = [{"role": "user", "content": "a" * 7}, {"role": "assistant", "content": "a" * 7}]
        result = count_messages(messages, lambda t: len(t))
        assert result == context_budget.TokenCount(14, True)

    def test_counts_serialized_tool_calls_as_real_payload(self):
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "get_thing", "arguments": {"id": 1}}}],
        }]
        with_calls = count_messages(messages, None).tokens
        without_calls = count_messages([{"role": "assistant", "content": ""}], None).tokens
        assert with_calls > without_calls

    def test_empty_list_is_trivially_measured(self):
        assert count_messages([], None) == context_budget.TokenCount(0, True)


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
        # Budget for only the last two units (character counter for determinism).
        result = fit_messages(messages, available_tokens=110, counter=len)
        assert result.messages[-1]["content"] == "new"
        assert not any("old" in m["content"] for m in result.messages)
        assert result.dropped_messages == 1

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
        assert result == context_budget.TrimResult([], 0, 0, True, 0, True)


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

    def test_measured_true_only_when_every_component_was_counted(self):
        outcome = enforce_budget(
            capacity_tokens=10_000,
            capacity_source="config",
            reserve_tokens=0,
            system_message="sys",
            messages=[_msg("user", "hi")],
            tool_schemas=[{"function": {"name": "f"}}],
            counter=len,
        )
        assert outcome.ledger["measured"] is True

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
