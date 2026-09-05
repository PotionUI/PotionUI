"""Tests for LLMGateway.test_configuration and its context-budget hook."""

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
        # config.max_tokens defaults to 2000; an override must win so the
        # reserve genuinely reflects what this call will ask the model for.
        outcome = gateway.estimate_context_budget(
            gateway.repository.get_configuration("cfg-1"), "", messages,
            options_override={"max_tokens": 4096},
        )
        assert outcome.ledger["reserve_tokens"] == 4096


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
