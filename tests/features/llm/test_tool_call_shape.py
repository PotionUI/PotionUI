"""Tool-call argument wire-shape normalization across providers.

Regression coverage for the Ollama 400 ("Value looks like object, but can't
find closing '}' symbol"): a completed tool round echoed back into history must
carry object arguments for Ollama and string arguments for OpenAI, regardless of
which shape the stored tool_call had.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from src.features.llm.clients import tool_call_shape
from src.features.llm.clients.ollama import OllamaClient
from src.features.llm.clients.openai import OpenAIClient
from src.features.llm.repository import LLMConfig


def _config(provider_type="ollama", **overrides):
    defaults = dict(
        id="cfg-1", name="Test", type=provider_type, enabled=True,
        base_url="http://localhost:11434", api_key=None, model="test-model",
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
    return client_cm, mock_client


def _stream_ctx(lines):
    async def _aiter_lines():
        for line in lines:
            yield line
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.aiter_lines = _aiter_lines
    mock_response.aread = AsyncMock(return_value=b"error")
    stream_cm = AsyncMock()
    stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=stream_cm)
    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    client_cm.__aexit__ = AsyncMock(return_value=False)
    return client_cm, mock_client


def _tool_round(arguments):
    """A completed tool round whose assistant tool_call carries *arguments*."""
    return [
        {"role": "user", "content": "echo hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "echo", "arguments": arguments}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "echo", "content": "echo: hi"},
        {"role": "user", "content": "thanks"},
    ]


def _payload_tool_args(payload):
    for m in payload["messages"]:
        if m["role"] == "assistant" and m.get("tool_calls"):
            return m["tool_calls"][0]["function"]["arguments"]
    raise AssertionError("no assistant tool_calls in payload")


class TestShapeHelpers:
    def test_arguments_to_object(self):
        assert tool_call_shape.arguments_to_object('{"a": 1}') == {"a": 1}
        assert tool_call_shape.arguments_to_object({"a": 1}) == {"a": 1}
        assert tool_call_shape.arguments_to_object(None) == {}
        assert tool_call_shape.arguments_to_object("not json") == {}
        assert tool_call_shape.arguments_to_object("[1, 2]") == {}

    def test_arguments_to_json_string(self):
        assert tool_call_shape.arguments_to_json_string({"a": 1}) == '{"a": 1}'
        assert tool_call_shape.arguments_to_json_string('{"a": 1}') == '{"a": 1}'
        assert tool_call_shape.arguments_to_json_string(None) == "{}"

    def test_normalize_does_not_mutate_input(self):
        original = [{"function": {"name": "x", "arguments": '{"a": 1}'}}]
        out = tool_call_shape.normalize_tool_calls(original, as_object=True)
        assert out[0]["function"]["arguments"] == {"a": 1}
        assert original[0]["function"]["arguments"] == '{"a": 1}'

    def test_normalize_tolerates_missing_function(self):
        assert tool_call_shape.normalize_tool_calls([{"id": "x"}], as_object=True) == [{"id": "x"}]
        assert tool_call_shape.normalize_tool_calls(None, as_object=True) is None


class TestOllamaShape:
    @pytest.mark.asyncio
    async def test_string_arguments_become_object_in_request(self):
        client_cm, mock_client = _post_ctx({"message": {"content": "ok"}, "eval_count": 1, "prompt_eval_count": 1})
        with patch("httpx.AsyncClient", return_value=client_cm):
            await OllamaClient().generate_with_tools(
                _tool_round('{"message": "hi"}'), _config("ollama"), "sys",
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
        args = _payload_tool_args(mock_client.post.call_args[1]["json"])
        assert args == {"message": "hi"}  # object, not a string → no 400

    @pytest.mark.asyncio
    async def test_object_arguments_pass_through_as_object(self):
        client_cm, mock_client = _post_ctx({"message": {"content": "ok"}, "eval_count": 1, "prompt_eval_count": 1})
        with patch("httpx.AsyncClient", return_value=client_cm):
            await OllamaClient().generate_with_tools(
                _tool_round({"message": "hi"}), _config("ollama"), "sys",
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
        assert _payload_tool_args(mock_client.post.call_args[1]["json"]) == {"message": "hi"}

    @pytest.mark.asyncio
    async def test_streaming_string_arguments_become_object(self):
        line = json.dumps({"message": {"content": "ok"}, "done": True, "eval_count": 1, "prompt_eval_count": 1})
        client_cm, mock_client = _stream_ctx([line])
        with patch("httpx.AsyncClient", return_value=client_cm):
            gen = OllamaClient().stream_with_tools(
                _tool_round('{"message": "hi"}'), _config("ollama"), "sys",
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
            async for _ in gen:
                pass
        payload = mock_client.stream.call_args[1]["json"]
        assert _payload_tool_args(payload) == {"message": "hi"}


class TestOpenAIShape:
    @pytest.mark.asyncio
    async def test_object_arguments_become_string_in_request(self):
        client_cm, mock_client = _post_ctx({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})
        with patch("httpx.AsyncClient", return_value=client_cm):
            await OpenAIClient().generate_with_tools(
                _tool_round({"message": "hi"}), _config("openai", base_url="http://localhost:1234/v1"), "sys",
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
        args = _payload_tool_args(mock_client.post.call_args[1]["json"])
        assert args == '{"message": "hi"}'  # string, the OpenAI wire shape

    @pytest.mark.asyncio
    async def test_string_arguments_pass_through_as_string(self):
        client_cm, mock_client = _post_ctx({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})
        with patch("httpx.AsyncClient", return_value=client_cm):
            await OpenAIClient().generate_with_tools(
                _tool_round('{"message": "hi"}'), _config("openai", base_url="http://localhost:1234/v1"), "sys",
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
        assert _payload_tool_args(mock_client.post.call_args[1]["json"]) == '{"message": "hi"}'


class TestRescueBuiltCallRoundTrips:
    """A rescue-built call (canonical object arguments) serializes correctly for both wires."""

    def _rescue_round(self):
        # Shape the executor's _rescue_final_content now builds: object arguments.
        return _tool_round({"operations": [{"op": "set_mode", "mode": "i2v"}]})

    @pytest.mark.asyncio
    async def test_ollama_gets_object(self):
        client_cm, mock_client = _post_ctx({"message": {"content": "ok"}, "eval_count": 1, "prompt_eval_count": 1})
        with patch("httpx.AsyncClient", return_value=client_cm):
            await OllamaClient().generate_with_tools(
                self._rescue_round(), _config("ollama"), "sys",
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
        assert _payload_tool_args(mock_client.post.call_args[1]["json"]) == {
            "operations": [{"op": "set_mode", "mode": "i2v"}]
        }

    @pytest.mark.asyncio
    async def test_openai_gets_string(self):
        client_cm, mock_client = _post_ctx({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}})
        with patch("httpx.AsyncClient", return_value=client_cm):
            await OpenAIClient().generate_with_tools(
                self._rescue_round(), _config("openai", base_url="http://localhost:1234/v1"), "sys",
                tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
            )
        args = _payload_tool_args(mock_client.post.call_args[1]["json"])
        assert json.loads(args) == {"operations": [{"op": "set_mode", "mode": "i2v"}]}
