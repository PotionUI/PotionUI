"""Completion-reason coverage for OpenAIClient's two BUFFERED (non-streaming)
entry points — generate_with_history and generate_with_tools.

Both read `choices[0].finish_reason` off the JSON response with no invented
default (a provider that omits it must normalize to "unknown", never "stop")
and populate `LLMResponse.completion` via `clients.completion.from_openai_compat`.
The streaming counterparts are covered in test_openai_stream_decoding.py; this
file exists because the buffered path parses the field independently (there is
no shared decoder between the two), so each needs its own present/absent proof.
"""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from src.features.llm.clients.openai import OpenAIClient
from src.features.llm.repository import LLMConfig

HISTORY = [{"role": "user", "content": "hello"}]


def _config(**overrides):
    defaults = dict(
        id="cfg-1", name="Test", type="openai", enabled=True,
        base_url="http://localhost:1234/v1", api_key=None, model="test-model",
        system_message="sys", temperature=0.7, max_tokens=512, timeout=30,
        supports_vision=False, disable_system_prompt=False, provider_options=None,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


def _post_ctx(response_data):
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value=response_data)
    mock_response.text = json.dumps(response_data)
    mock_response.raise_for_status = Mock()
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    client_cm.__aexit__ = AsyncMock(return_value=False)
    return client_cm


def _choice(finish_reason=None, **extra):
    choice = {"message": {"content": "hi", **extra}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return choice


@pytest.fixture
def client():
    return OpenAIClient()


class TestGenerateWithHistoryCompletion:
    @pytest.mark.asyncio
    async def test_present_length_normalizes_to_length(self, client):
        with patch("httpx.AsyncClient", return_value=_post_ctx(
            {"choices": [_choice("length")], "usage": {}}
        )):
            response = await client.generate_with_history(HISTORY, _config(), "sys")
        assert response.finish_reason == "length"
        assert response.completion == {"reason": "length", "raw": "length"}

    @pytest.mark.asyncio
    async def test_present_stop_normalizes_to_stop(self, client):
        with patch("httpx.AsyncClient", return_value=_post_ctx(
            {"choices": [_choice("stop")], "usage": {}}
        )):
            response = await client.generate_with_history(HISTORY, _config(), "sys")
        assert response.finish_reason == "stop"
        assert response.completion == {"reason": "stop", "raw": "stop"}

    @pytest.mark.asyncio
    async def test_absent_reason_is_unknown_never_invented_as_stop(self, client):
        with patch("httpx.AsyncClient", return_value=_post_ctx(
            {"choices": [_choice(None)], "usage": {}}
        )):
            response = await client.generate_with_history(HISTORY, _config(), "sys")
        assert response.finish_reason is None
        assert response.completion == {"reason": "unknown", "raw": None}


class TestGenerateWithToolsCompletion:
    @pytest.mark.asyncio
    async def test_present_length_normalizes_to_length(self, client):
        with patch("httpx.AsyncClient", return_value=_post_ctx(
            {"choices": [_choice("length")], "usage": {}}
        )):
            response = await client.generate_with_tools(HISTORY, _config(), "sys")
        assert response.finish_reason == "length"
        assert response.completion == {"reason": "length", "raw": "length"}

    @pytest.mark.asyncio
    async def test_present_stop_normalizes_to_stop(self, client):
        with patch("httpx.AsyncClient", return_value=_post_ctx(
            {"choices": [_choice("stop")], "usage": {}}
        )):
            response = await client.generate_with_tools(HISTORY, _config(), "sys")
        assert response.finish_reason == "stop"
        assert response.completion == {"reason": "stop", "raw": "stop"}

    @pytest.mark.asyncio
    async def test_absent_reason_is_unknown_never_invented_as_stop(self, client):
        with patch("httpx.AsyncClient", return_value=_post_ctx(
            {"choices": [_choice(None)], "usage": {}}
        )):
            response = await client.generate_with_tools(HISTORY, _config(), "sys")
        assert response.finish_reason is None
        assert response.completion == {"reason": "unknown", "raw": None}

    @pytest.mark.asyncio
    async def test_present_tool_calls_normalizes_to_tool_calls(self, client):
        with patch("httpx.AsyncClient", return_value=_post_ctx(
            {"choices": [_choice("tool_calls", tool_calls=[
                {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}
            ])], "usage": {}}
        )):
            response = await client.generate_with_tools(
                HISTORY, _config(), "sys",
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
        assert response.finish_reason == "tool_calls"
        assert response.completion == {"reason": "tool_calls", "raw": "tool_calls"}
