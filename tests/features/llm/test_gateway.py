"""Tests for LLMGateway.test_configuration."""

import pytest
from unittest.mock import Mock, AsyncMock, patch

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
