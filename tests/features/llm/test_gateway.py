"""Tests for LLMGateway.test_configuration and its context-budget hook."""

import asyncio

import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.llm import context_budget
from src.features.llm.gateway import LLMGateway
from src.features.llm.repository import LLMConfig


def make_config(**overrides) -> LLMConfig:
    defaults = dict(
        id="cfg-1",
        name="Test Config",
        type="ollama",
        enabled=True,
        base_url="http://internal-secret-host:11434",
        model="llama3",
        system_message="You are helpful.",
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


class TestGatewayTestConfiguration:
    @pytest.fixture
    def gateway(self):
        return LLMGateway(llm_repository=Mock())

    @pytest.mark.asyncio
    async def test_success_reports_response(self, gateway):
        config = make_config()
        response = Mock(content="Test successful!", model="llama3", tokens_used=12)
        gateway.generate_response = AsyncMock(return_value=response)

        result = await gateway.test_configuration(config)

        assert result["success"] is True
        assert result["response"] == "Test successful!"
        assert result["model"] == "llama3"
        assert result["tokens_used"] == 12

    @pytest.mark.asyncio
    async def test_failure_does_not_leak_exception_detail(self, gateway):
        config = make_config()
        secret_detail = "connection failed to http://internal-secret-host:11434 using key sk-ABC123XYZ"
        gateway.generate_response = AsyncMock(side_effect=RuntimeError(secret_detail))

        with patch("src.features.llm.gateway.logging") as mock_logging:
            result = await gateway.test_configuration(config)

        assert result["success"] is False
        assert "error" in result
        assert secret_detail not in result["error"]
        assert "sk-ABC123XYZ" not in result["error"]
        assert "internal-secret-host" not in result["error"]

        mock_logging.error.assert_called_once()
        _, kwargs = mock_logging.error.call_args
        assert kwargs.get("exc_info") is True


class TestGenerateResponseClampsMaxTokensOnAKnownWindow:
    @pytest.mark.asyncio
    async def test_known_window_clamps_max_tokens_sent_to_the_client(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(provider_options={"context_window": 32768}, max_tokens=50000)
        gateway._ollama.generate = AsyncMock(return_value=Mock(content="ok"))

        await gateway.generate_response("hi", config, config.system_message)

        sent_config = gateway._ollama.generate.call_args.args[1]
        expected = gateway.estimate_context_budget(
            config, config.system_message, [{"role": "user", "content": "hi"}],
        ).ledger["max_tokens_sent"]
        assert sent_config.max_tokens == expected
        assert sent_config.max_tokens < 50000

    @pytest.mark.asyncio
    async def test_unknown_window_leaves_max_tokens_untouched(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(max_tokens=100_000)
        gateway._ollama.generate = AsyncMock(return_value=Mock(content="ok"))

        await gateway.generate_response("hi", config, config.system_message)

        sent_config = gateway._ollama.generate.call_args.args[1]
        assert sent_config.max_tokens == 100_000


async def _empty_stream(*args, **kwargs):
    return
    yield  # pragma: no cover - makes this an async generator


class TestGatewayContextBudgetHook:
    """The budget check must run on every one of the four send paths — the
    guard here patches `_budgeted` itself so a provider-specific bypass
    (a new send method added later that forgets to call it) fails loudly.
    """

    @pytest.fixture
    def gateway(self):
        gw = LLMGateway(llm_repository=Mock())
        gw.repository.get_configuration.return_value = make_config()
        gw._ollama.generate_with_history = AsyncMock(return_value=Mock(
            content="ok", tool_calls=None, tokens_used=1, prompt_tokens=1, completion_tokens=0,
        ))
        gw._ollama.stream_with_history = _empty_stream
        gw._ollama.generate_with_tools = AsyncMock(return_value=Mock(
            content="ok", tool_calls=None, tokens_used=1, prompt_tokens=1, completion_tokens=0,
        ))
        gw._ollama.stream_with_tools = _empty_stream
        return gw

    @pytest.mark.asyncio
    async def test_budget_hook_runs_on_every_send_method(self, gateway):
        messages = [{"role": "user", "content": "hello"}]
        with patch.object(gateway, "_budgeted", wraps=gateway._budgeted) as spy:
            await gateway.generate_with_history(messages=messages, llm_id="cfg-1")
            assert spy.call_count == 1

            async for _ in gateway.stream_with_history(messages=messages, llm_id="cfg-1"):
                pass
            assert spy.call_count == 2

            await gateway.generate_with_tools(messages=messages, llm_id="cfg-1")
            assert spy.call_count == 3

            async for _ in gateway.stream_with_tools(messages=messages, llm_id="cfg-1"):
                pass
            assert spy.call_count == 4

    @pytest.mark.asyncio
    async def test_trims_before_the_client_ever_sees_the_messages(self, gateway):
        gateway.repository.get_configuration.return_value = make_config(
            provider_options={"context_window": 200}, max_tokens=50,
        )
        big_history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 200}
            for i in range(10)
        ] + [{"role": "user", "content": "current question"}]

        await gateway.generate_with_history(messages=big_history, llm_id="cfg-1")

        sent = gateway._ollama.generate_with_history.call_args.args[0]
        assert len(sent) < len(big_history)
        assert sent[-1]["content"] == "current question"

    @pytest.mark.asyncio
    async def test_irreducibly_oversized_request_raises_instead_of_sending(self, gateway):
        gateway.repository.get_configuration.return_value = make_config(
            provider_options={"context_window": 10}, max_tokens=5,
        )
        messages = [{"role": "user", "content": "x" * 500}]

        with pytest.raises(context_budget.ContextBudgetExceededError):
            await gateway.generate_with_history(messages=messages, llm_id="cfg-1")

        gateway._ollama.generate_with_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_reserve_tokens_follow_options_override_max_tokens(self, gateway):
        messages = [{"role": "user", "content": "hi"}]
        await gateway.generate_with_history(
            messages=messages, llm_id="cfg-1", options_override={"max_tokens": 4096},
        )
        outcome = gateway.estimate_context_budget(
            gateway.repository.get_configuration("cfg-1"), "", messages,
            options_override={"max_tokens": 4096},
        )
        assert outcome.ledger["max_tokens"] == 4096
        assert outcome.ledger["reserve_tokens"] == 2049


class TestGatewayTokenCounterFor:
    def test_no_provider_counter_returns_none(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(type="ollama")
        assert gateway.token_counter_for(config) is None

    def test_uses_the_native_client_counter_when_present(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(type="native", model="native-model")
        gateway._native.token_counter = Mock(return_value=lambda text: 7)
        counter = gateway.token_counter_for(config)
        assert counter("anything") == 7

    def test_a_raising_provider_counter_degrades_to_none(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(type="native", model="native-model")
        gateway._native.token_counter = Mock(side_effect=RuntimeError("boom"))
        assert gateway.token_counter_for(config) is None


class TestGatewayContextProfileFor:
    """`context_profile_for` is the single shared builder — both the real
    send hook and ConversationRunner's pre-flight ledger call it, so both
    must see exactly this bundle for a given config/options_override."""

    def test_bundles_capacity_reserve_counter_and_image_override(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(
            type="native", model="native-model",
            provider_options={"context_window": 4096, "image_token_estimate": 200},
            max_tokens=777,
        )
        gateway._native.token_counter = Mock(return_value=lambda text: len(text))
        gateway._native.messages_token_counter = Mock(return_value=lambda s, m, t: 99)

        inputs = gateway.context_profile_for(config)

        assert inputs.capacity == context_budget.CapacityInfo(4096, "config")
        assert inputs.reserve_tokens == 777
        assert inputs.counter("hi") == 2
        assert inputs.messages_counter(None, [], None) == 99
        assert inputs.image_tokens_override == 200

    def test_options_override_max_tokens_wins_over_config_default(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(max_tokens=2000, provider_options={"think": False})
        inputs = gateway.context_profile_for(config, options_override={"max_tokens": 4096})
        assert inputs.max_tokens == 4096
        assert inputs.reserve_tokens == 1229

    def test_a_raising_messages_token_counter_hook_degrades_to_none(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(type="native", model="native-model")
        gateway._native.messages_token_counter = Mock(side_effect=RuntimeError("boom"))
        inputs = gateway.context_profile_for(config)
        assert inputs.messages_counter is None

    def test_ollama_openai_never_get_a_messages_counter(self):
        gateway = LLMGateway(llm_repository=Mock())
        for provider_type in ("ollama", "openai"):
            inputs = gateway.context_profile_for(make_config(type=provider_type))
            assert inputs.messages_counter is None


class TestGatewayReserveClampRegression:

    @pytest.fixture
    def gateway(self):
        gw = LLMGateway(llm_repository=Mock())
        gw._ollama.generate_with_history = AsyncMock(return_value=Mock(
            content="ok", tool_calls=None, tokens_used=1, prompt_tokens=1, completion_tokens=0,
        ))
        return gw

    def _history(self, turns=6):
        history = []
        for i in range(turns):
            history.append({"role": "user", "content": f"turn {i} question " * 20})
            history.append({"role": "assistant", "content": f"turn {i} answer " * 20})
        history.append({"role": "user", "content": "current question"})
        return history

    @pytest.mark.asyncio
    async def test_configured_window_with_large_max_tokens_now_includes_prior_turns(self, gateway):
        gateway.repository.get_configuration.return_value = make_config(
            provider_options={"context_window": 32768}, max_tokens=50000,
        )
        history = self._history()

        await gateway.generate_with_history(messages=history, llm_id="cfg-1")

        sent = gateway._ollama.generate_with_history.call_args.args[0]
        assert len(sent) > 1
        assert sent[-1]["content"] == "current question"

    def test_configured_window_with_large_max_tokens_clamps_the_reserve(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(
            provider_options={"context_window": 32768, "think": False}, max_tokens=50000,
        )
        outcome = gateway.estimate_context_budget(config, "sys", [{"role": "user", "content": "hi"}])
        assert outcome.ledger["reserve_tokens"] < outcome.ledger["max_tokens"]
        assert outcome.ledger["reserve_tokens"] == 4916

    def test_unknown_window_with_large_max_tokens_clamps_the_reserve(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(provider_options={"think": False}, max_tokens=100_000)
        outcome = gateway.estimate_context_budget(config, "sys", [{"role": "user", "content": "hi"}])
        assert outcome.ledger["capacity_source"] == "unknown"
        assert outcome.ledger["reserve_tokens"] == 1229
        assert outcome.ledger["reserve_tokens"] < outcome.ledger["max_tokens"]

    @pytest.mark.asyncio
    async def test_unknown_window_does_not_clamp_what_is_sent_as_max_tokens(self, gateway):
        gateway.repository.get_configuration.return_value = make_config(
            type="ollama", max_tokens=100_000,
        )

        await gateway.generate_with_history(messages=[{"role": "user", "content": "hi"}], llm_id="cfg-1")

        sent_override = gateway._ollama.generate_with_history.call_args.args[4]
        assert sent_override is None

    def test_thinking_enabled_adds_a_reserve_allowance(self):
        gateway = LLMGateway(llm_repository=Mock())
        thinking_off = make_config(
            type="ollama", provider_options={"context_window": 32768, "think": False}, max_tokens=1000,
        )
        thinking_on = make_config(
            type="ollama", provider_options={"context_window": 32768, "think": True}, max_tokens=1000,
        )
        off = gateway.context_profile_for(thinking_off)
        on = gateway.context_profile_for(thinking_on)

        assert off.thinking_enabled is False
        assert on.thinking_enabled is True
        assert on.reserve_tokens > off.reserve_tokens
        assert on.reserve_tokens == off.reserve_tokens + on.thinking_allowance_tokens

    def test_force_prompt_tools_with_tools_offered_and_unset_think_reserves_the_allowance(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(
            type="ollama", provider_options={"context_window": 32768, "force_prompt_tools": True}, max_tokens=1000,
        )
        tools = [{"type": "function", "function": {"name": "noop", "parameters": {}}}]

        profile = gateway.context_profile_for(config, tool_schemas=tools, force_prompt_tools_eligible=True)

        assert profile.thinking_enabled is True
        assert profile.thinking_allowance_tokens > 0

    def test_native_tools_with_unset_think_does_not_reserve_the_allowance(self):
        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(
            type="ollama", provider_options={"context_window": 32768}, max_tokens=1000,
        )
        tools = [{"type": "function", "function": {"name": "noop", "parameters": {}}}]

        profile = gateway.context_profile_for(config, tool_schemas=tools, force_prompt_tools_eligible=True)

        assert profile.thinking_enabled is False
        assert profile.thinking_allowance_tokens == 0

    @pytest.mark.asyncio
    async def test_preflight_ledger_and_real_send_agree_on_the_new_fields(self, gateway):
        from src.features.chat.conversation import ConversationRunner

        config = make_config(provider_options={"context_window": 32768}, max_tokens=50000)
        gateway.repository.get_configuration.return_value = config
        messages = [{"role": "user", "content": "hi"}]

        await gateway.generate_with_history(messages=list(messages), llm_id="cfg-1")
        send_outcome = gateway.estimate_context_budget(config, "", list(messages))

        session = Mock(llm_config_id="cfg-1")
        manager = Mock()
        manager.llm_service = gateway
        runner = ConversationRunner(manager)
        accounting = runner._resolve_budget_inputs(session, mode=None)
        preflight_ledger = ConversationRunner._build_context_ledger(
            "", [], {}, list(messages), accounting=accounting,
        )

        assert preflight_ledger["budget"]["reserve_tokens"] == send_outcome.ledger["reserve_tokens"]
        assert preflight_ledger["budget"]["max_tokens"] == send_outcome.ledger["max_tokens"]
        assert preflight_ledger["budget"]["max_tokens_sent"] == send_outcome.ledger["max_tokens_sent"]
        assert preflight_ledger["budget"]["thinking_enabled"] == send_outcome.ledger["thinking_enabled"]


class TestSentMaxTokensNeverExceedsWindow:
    WINDOW = 32768

    def _long_history(self):
        history = []
        for i in range(20):
            history.append({"role": "user", "content": f"turn {i} question " * 200})
            history.append({"role": "assistant", "content": f"turn {i} answer " * 200})
        history.append({"role": "user", "content": "current question"})
        return history

    def _build_gateway(self, provider_type):
        gw = LLMGateway(llm_repository=Mock())
        response = Mock(content="ok", tool_calls=None, tokens_used=1, prompt_tokens=1, completion_tokens=0)
        client = getattr(gw, f"_{provider_type}")
        client.generate_with_history = AsyncMock(return_value=response)
        client.generate_with_tools = AsyncMock(return_value=response)
        client.stream_with_history = Mock(side_effect=_empty_stream)
        client.stream_with_tools = Mock(side_effect=_empty_stream)
        return gw

    @pytest.mark.asyncio
    @pytest.mark.parametrize("provider_type", ["openai", "ollama"])
    @pytest.mark.parametrize("max_tokens", [8192, 30000, 50000])
    @pytest.mark.parametrize("send_method,uses_tools,options_override_index", [
        ("generate_with_history", False, 4),
        ("stream_with_history", False, 4),
        ("generate_with_tools", True, 5),
        ("stream_with_tools", True, 5),
    ])
    async def test_prompt_estimate_plus_received_max_tokens_fits_the_window(
        self, provider_type, max_tokens, send_method, uses_tools, options_override_index,
    ):
        gateway = self._build_gateway(provider_type)
        config = make_config(
            type=provider_type, provider_options={"context_window": self.WINDOW}, max_tokens=max_tokens,
        )
        gateway.repository.get_configuration.return_value = config
        history = self._long_history()
        tools = [{"type": "function", "function": {"name": "noop", "parameters": {}}}] if uses_tools else None

        method = getattr(gateway, send_method)
        kwargs = {"messages": list(history), "llm_id": "cfg-1"}
        if uses_tools:
            kwargs["tools"] = tools
        if send_method.startswith("stream"):
            async for _ in method(**kwargs):
                pass
        else:
            await method(**kwargs)

        client = getattr(gateway, f"_{provider_type}")
        call = getattr(client, send_method).call_args
        sent_messages = call.args[0]
        sent_system_message = call.args[2]
        sent_options_override = call.args[options_override_index]
        received_max_tokens = (sent_options_override or {}).get("max_tokens", config.max_tokens)

        estimated_prompt = (
            context_budget.count_text(sent_system_message, None).tokens
            + context_budget.count_messages(sent_messages, None).tokens
            + context_budget.count_tool_schemas(tools, None).tokens
        )

        assert estimated_prompt + received_max_tokens <= self.WINDOW
        assert received_max_tokens <= max_tokens


class TestGatewayImageTokenOverrideThreading:
    """A `provider_options.image_token_estimate` override must actually
    reach the real budget check, not just exist as an unused parameter."""

    @pytest.fixture
    def gateway(self):
        gw = LLMGateway(llm_repository=Mock())
        gw._ollama.generate_with_history = AsyncMock(return_value=Mock(
            content="ok", tool_calls=None, tokens_used=1, prompt_tokens=1, completion_tokens=0,
        ))
        return gw

    @pytest.mark.asyncio
    async def test_override_changes_the_enforced_image_cost(self, gateway):
        gateway.repository.get_configuration.return_value = make_config(
            provider_options={"context_window": 10_000, "image_token_estimate": 5},
        )
        messages = [{"role": "user", "content": "hi"}]

        with patch.object(context_budget, "enforce_budget", wraps=context_budget.enforce_budget) as spy:
            await gateway.generate_with_history(
                messages=messages, llm_id="cfg-1", image_data="base64...",
            )

        assert spy.call_args.kwargs["image_tokens"] == 5

    @pytest.mark.asyncio
    async def test_no_override_leaves_image_tokens_unset(self, gateway):
        gateway.repository.get_configuration.return_value = make_config(
            provider_options={"context_window": 10_000},
        )
        messages = [{"role": "user", "content": "hi"}]

        with patch.object(context_budget, "enforce_budget", wraps=context_budget.enforce_budget) as spy:
            await gateway.generate_with_history(
                messages=messages, llm_id="cfg-1", image_data="base64...",
            )

        assert spy.call_args.kwargs["image_tokens"] is None


class TestLedgerMatchesGatewayHook:
    """ConversationRunner's pre-flight ledger and the gateway's real send
    hook must compute identical numbers for the identical request — they
    share `context_profile_for`, so this pins that down end to end."""

    @pytest.mark.asyncio
    async def test_same_config_and_request_yield_the_same_ledger(self):
        from src.features.chat.conversation import ConversationRunner

        gateway = LLMGateway(llm_repository=Mock())
        config = make_config(
            type="native", model="native-model",
            provider_options={"context_window": 500, "image_token_estimate": 30},
            max_tokens=64,
        )
        gateway.repository.get_configuration.return_value = config
        gateway._native.token_counter = Mock(return_value=lambda text: len(text))
        gateway._native.messages_token_counter = Mock(return_value=lambda system_message, messages, tools: 37)
        gateway._native.generate_with_history = AsyncMock(return_value=Mock(
            content="ok", tool_calls=None, tokens_used=1, prompt_tokens=1, completion_tokens=0,
        ))

        messages = [{"role": "user", "content": "a distinctive question"}]
        system_message = "You are helpful."
        image_data = "base64imagebytes..."

        gateway_outcome = await gateway.generate_with_history(
            messages=list(messages), llm_id="cfg-1", custom_system_message=system_message,
            image_data=image_data,
        )
        gateway_budget = gateway.estimate_context_budget(
            config, system_message, list(messages), image_data=image_data,
        )
        assert gateway_budget.ledger["image_tokens"] == 30
        assert gateway_budget.ledger["accounting"] == "chat_template"

        session = Mock(llm_config_id="cfg-1")
        manager = Mock()
        manager.llm_service = gateway
        runner = ConversationRunner(manager)
        accounting = runner._resolve_budget_inputs(session, mode=None)
        ledger = ConversationRunner._build_context_ledger(
            system_message, [], {}, list(messages), accounting=accounting, image_data=image_data,
        )

        assert ledger["budget"] == gateway_budget.ledger


# ---------------------------------------------------------------------------
# LLM-10: closing the gateway's own stream must close the client's stream at
# the same boundary, not defer it to GC/loop shutdown. See also
# tests/features/llm/tools/test_tool_workflow_cancellation.py, which pins the
# same contract one layer up (through ToolExecutor.execute_with_tools_stream).
# ---------------------------------------------------------------------------

class _ParkingClient:
    """Client double that yields one token, then parks forever in an await —
    exactly the shape a real provider stream takes while waiting on its next
    network read. `markers` records whether its `finally` actually ran."""

    def __init__(self):
        self.markers: list[str] = []

    async def stream_with_history(self, *args, **kwargs):
        try:
            yield {"type": "token", "content": "hi"}
            await asyncio.Event().wait()
        finally:
            self.markers.append("client_stream_closed")

    def stream_with_tools(self, *args, **kwargs):
        # Tool-calling turns share the same wire shape here — reuse the same
        # generator rather than duplicate it.
        return self.stream_with_history(*args, **kwargs)


class TestGatewayStreamClosePropagation:
    @pytest.fixture
    def gateway(self):
        gw = LLMGateway(llm_repository=Mock())
        gw.repository.get_configuration.return_value = make_config()
        return gw

    @pytest.mark.asyncio
    async def test_closing_stream_with_history_closes_the_client_at_once(self, gateway):
        client = _ParkingClient()
        gateway._ollama.stream_with_history = client.stream_with_history

        agen = gateway.stream_with_history(messages=[{"role": "user", "content": "hi"}], llm_id="cfg-1")
        first = await agen.__anext__()
        assert first == {"type": "token", "content": "hi"}
        assert client.markers == []  # not yet — the client is still suspended

        await agen.aclose()

        assert client.markers == ["client_stream_closed"]

    @pytest.mark.asyncio
    async def test_closing_stream_with_tools_closes_the_client_at_once(self, gateway):
        client = _ParkingClient()
        gateway._ollama.stream_with_tools = client.stream_with_tools

        agen = gateway.stream_with_tools(messages=[{"role": "user", "content": "hi"}], llm_id="cfg-1")
        first = await agen.__anext__()
        assert first == {"type": "token", "content": "hi"}
        assert client.markers == []

        await agen.aclose()

        assert client.markers == ["client_stream_closed"]
