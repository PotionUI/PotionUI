"""Tests for LLMGateway per-call sampling overrides (options_override) and the
OpenAI sampling passthrough fix.

Covers:
- generate_with_history (Ollama): options_override maps to options.temperature,
  options.num_predict, options.top_p, and top-level think; config values used
  when no override given.
- generate_with_history (OpenAI): provider_options top_p is forwarded; options_override
  temperature wins over config.temperature.
"""

import json
import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, Mock, patch

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.features.llm.gateway import LLMGateway
from src.features.llm.repository import LLMConfig


def _make_config(provider_type: str = "ollama", **overrides) -> LLMConfig:
    defaults = dict(
        id="cfg-1",
        name="Test Config",
        type=provider_type,
        enabled=True,
        base_url="http://localhost:11434",
        api_key=None,
        model="test-model",
        system_message="You are helpful.",
        temperature=0.7,
        max_tokens=512,
        timeout=30,
        supports_vision=False,
        disable_system_prompt=False,
        provider_options=None,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


def _make_mock_repo(config: LLMConfig | None = None):
    repo = Mock()
    repo.get_configuration = Mock(return_value=config)
    return repo


def _make_service(config: LLMConfig | None = None, repo=None):
    if repo is None:
        repo = _make_mock_repo(config)
    return LLMGateway(llm_repository=repo)


def _make_post_ctx(status_code: int, response_data: dict):
    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.json = Mock(return_value=response_data)
    mock_response.text = json.dumps(response_data)
    mock_response.raise_for_status = Mock()

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    client_cm.__aexit__ = AsyncMock(return_value=False)

    return client_cm, mock_client, mock_response


class TestGenerateWithHistoryOllamaOptionsOverride:

    @pytest.mark.asyncio
    async def test_options_override_applied(self):
        config = _make_config("ollama", temperature=0.5, max_tokens=200)
        service = _make_service(config)

        response_data = {"message": {"content": "hi"}, "eval_count": 1, "prompt_eval_count": 1}
        client_cm, mock_client, _ = _make_post_ctx(200, response_data)

        override = {"temperature": 1.1, "max_tokens": 600, "think": False, "top_p": 0.95}
        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await service.generate_with_history(messages, llm_id="cfg-1", options_override=override)

        payload = mock_client.post.call_args[1]["json"]
        assert payload["options"]["temperature"] == 1.1
        assert payload["options"]["num_predict"] == 600
        assert payload["options"]["top_p"] == 0.95
        assert payload["think"] is False

    @pytest.mark.asyncio
    async def test_no_override_uses_config_values(self):
        config = _make_config("ollama", temperature=0.5, max_tokens=200)
        service = _make_service(config)

        response_data = {"message": {"content": "hi"}, "eval_count": 1, "prompt_eval_count": 1}
        client_cm, mock_client, _ = _make_post_ctx(200, response_data)

        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await service.generate_with_history(messages, llm_id="cfg-1")

        payload = mock_client.post.call_args[1]["json"]
        assert payload["options"]["temperature"] == 0.5
        assert payload["options"]["num_predict"] == 200
        assert payload["think"] is True


class TestGenerateWithHistoryOpenAISamplingPassthrough:

    @pytest.mark.asyncio
    async def test_top_p_forwarded_from_provider_options(self):
        config = _make_config(
            "openai", base_url="http://api.openai.test/v1", provider_options={"top_p": 0.8}
        )
        service = _make_service(config)

        response_data = {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"total_tokens": 1, "prompt_tokens": 1, "completion_tokens": 1}
        }
        client_cm, mock_client, _ = _make_post_ctx(200, response_data)

        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await service.generate_with_history(messages, llm_id="cfg-1")

        payload = mock_client.post.call_args[1]["json"]
        assert payload["top_p"] == 0.8

    @pytest.mark.asyncio
    async def test_options_override_temperature_wins(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1", temperature=0.3)
        service = _make_service(config)

        response_data = {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"total_tokens": 1, "prompt_tokens": 1, "completion_tokens": 1}
        }
        client_cm, mock_client, _ = _make_post_ctx(200, response_data)

        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await service.generate_with_history(
                messages, llm_id="cfg-1", options_override={"temperature": 0.99}
            )

        payload = mock_client.post.call_args[1]["json"]
        assert payload["temperature"] == 0.99
