"""Tests for LLMGateway streaming methods.

Covers:
- OllamaClient.stream_with_history: yields correct content chunks, skips empty content,
  stops on done=True, handles malformed JSON, raises on non-200 status
- OpenAIClient.stream_with_history: yields correct content chunks from SSE lines,
  skips non-data lines, stops on [DONE], handles malformed JSON,
  raises on non-200 status
- stream_with_history: routes to the correct provider, raises on unknown
  provider type, raises when config is missing or disabled
- Connection/network error propagation for both providers
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        provider_options=None,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


def _make_mock_repo(config: LLMConfig | None = None):
    """Return a minimal mock LLMRepository."""
    repo = Mock()
    repo.get_configuration = Mock(return_value=config)
    return repo


def _make_service(config: LLMConfig | None = None, repo=None):
    """Create an LLMGateway with a mocked repository.

    LLMGateway.__init__ uses @inject and expects 'llm_repository' as the
    parameter name (not 'repository').
    """
    if repo is None:
        repo = _make_mock_repo(config)
    return LLMGateway(llm_repository=repo)


async def _collect(async_gen):
    """Collect all values yielded by an async generator into a list."""
    result = []
    async for item in async_gen:
        result.append(item)
    return result


async def _collect_tokens(async_gen):
    """Collect only token content strings from a dict-yielding stream.

    Streaming methods now yield {"type": "token", "content": ...} and
    {"type": "usage", ...} dicts. This helper extracts just the content strings.
    """
    result = []
    async for item in async_gen:
        if isinstance(item, dict) and item.get("type") == "token":
            result.append(item["content"])
    return result


# ---------------------------------------------------------------------------
# Async mock context-manager helpers
# ---------------------------------------------------------------------------

def _make_stream_ctx(status_code: int, lines: list[str]):
    """Build the nested async context managers that httpx.AsyncClient.stream() returns.

    Usage in production code:
        async with httpx.AsyncClient(timeout=...) as client:
            async with client.stream("POST", url, ...) as response:
                ...
    """

    # Inner: the streaming response object
    async def _aiter_lines():
        for line in lines:
            yield line

    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.aiter_lines = _aiter_lines

    # aread() is called when status_code != 200 to get the error body
    mock_response.aread = AsyncMock(return_value=b"error body")

    # Make `response` work as async context manager
    stream_cm = AsyncMock()
    stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
    stream_cm.__aexit__ = AsyncMock(return_value=False)

    # The client mock returned by `async with httpx.AsyncClient(...) as client:`
    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=stream_cm)

    # Make `client` work as async context manager
    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    client_cm.__aexit__ = AsyncMock(return_value=False)

    return client_cm, mock_client, mock_response


# ---------------------------------------------------------------------------
# OllamaClient.stream_with_history
# ---------------------------------------------------------------------------

class TestStreamOllamaWithHistory:

    def _service(self, config=None):
        return _make_service(config)

    def _ollama_line(self, content: str, done: bool = False) -> str:
        return json.dumps({"message": {"content": content}, "done": done})

    @pytest.mark.asyncio
    async def test_yields_content_chunks(self):
        config = _make_config("ollama")
        service = self._service(config)

        lines = [
            self._ollama_line("Hello"),
            self._ollama_line(", "),
            self._ollama_line("world!", done=True),
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._ollama.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert chunks == ["Hello", ", ", "world!"]

    @pytest.mark.asyncio
    async def test_skips_empty_content(self):
        config = _make_config("ollama")
        service = self._service(config)

        lines = [
            json.dumps({"message": {"content": ""}, "done": False}),
            self._ollama_line("text"),
            self._ollama_line("", done=True),
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._ollama.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert chunks == ["text"]

    @pytest.mark.asyncio
    async def test_stops_on_done_true(self):
        """No further chunks should be yielded after done=True."""
        config = _make_config("ollama")
        service = self._service(config)

        lines = [
            self._ollama_line("first", done=False),
            self._ollama_line("STOP", done=True),
            # This line should never be reached
            self._ollama_line("after-done", done=False),
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._ollama.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert "after-done" not in chunks

    @pytest.mark.asyncio
    async def test_skips_blank_lines(self):
        config = _make_config("ollama")
        service = self._service(config)

        lines = [
            "",
            self._ollama_line("chunk"),
            "",
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._ollama.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert chunks == ["chunk"]

    @pytest.mark.asyncio
    async def test_skips_malformed_json_lines(self):
        config = _make_config("ollama")
        service = self._service(config)

        lines = [
            "not-json",
            self._ollama_line("valid"),
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._ollama.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        # Malformed JSON line is silently skipped; valid chunk still yielded
        assert chunks == ["valid"]

    @pytest.mark.asyncio
    async def test_yields_usage_on_done(self):
        """The final done chunk should produce a usage event with token counts."""
        config = _make_config("ollama")
        service = self._service(config)

        done_line = json.dumps({
            "message": {"content": "end"},
            "done": True,
            "eval_count": 42,
            "prompt_eval_count": 10,
        })
        lines = [done_line]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            all_events = await _collect(
                service._ollama.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        usage_events = [e for e in all_events if e.get("type") == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0]["completion_tokens"] == 42
        assert usage_events[0]["prompt_tokens"] == 10
        assert usage_events[0]["tokens_used"] == 52

    @pytest.mark.asyncio
    async def test_raises_on_non_200_status(self):
        config = _make_config("ollama")
        service = self._service(config)

        client_cm, _, _ = _make_stream_ctx(500, [])

        with patch("httpx.AsyncClient", return_value=client_cm):
            with pytest.raises(ValueError, match="Ollama returned status 500"):
                await _collect(
                    service._ollama.stream_with_history(
                        [{"role": "user", "content": "hi"}], config, "sys"
                    )
                )

    @pytest.mark.asyncio
    async def test_image_data_attached_to_last_user_message(self):
        """When image_data is provided, the last user message receives an 'images' key."""
        config = _make_config("ollama")
        service = self._service(config)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        messages = [{"role": "user", "content": "describe this"}]
        image_data = "base64encodedimage"

        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service._ollama.stream_with_history(
                    messages, config, "sys", image_data=image_data
                )
            )

        # Verify the stream call was made with the image in the payload
        call_kwargs = mock_client.stream.call_args[1]
        json_payload = call_kwargs["json"]
        user_msgs = [m for m in json_payload["messages"] if m["role"] == "user"]
        assert user_msgs, "Expected at least one user message in payload"
        assert user_msgs[-1].get("images") == [image_data]

    @pytest.mark.asyncio
    async def test_provider_options_forwarded(self):
        """num_ctx from provider_options should be in the request options."""
        config = _make_config("ollama", provider_options={"num_ctx": 4096})
        service = self._service(config)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service._ollama.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        call_kwargs = mock_client.stream.call_args[1]
        assert call_kwargs["json"]["options"]["num_ctx"] == 4096

    @pytest.mark.asyncio
    async def test_think_defaults_to_true_when_not_set(self):
        config = _make_config("ollama", provider_options={})
        service = self._service(config)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service._ollama.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        call_kwargs = mock_client.stream.call_args[1]
        assert call_kwargs["json"]["think"] is True

    @pytest.mark.asyncio
    async def test_stream_flag_is_true(self):
        """The payload must request streaming from Ollama."""
        config = _make_config("ollama")
        service = self._service(config)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service._ollama.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        call_kwargs = mock_client.stream.call_args[1]
        assert call_kwargs["json"]["stream"] is True


# ---------------------------------------------------------------------------
# OpenAIClient.stream_with_history
# ---------------------------------------------------------------------------

class TestStreamOpenaiWithHistory:

    def _service(self, config=None):
        return _make_service(config)

    def _sse_line(self, content: str) -> str:
        data = {"choices": [{"delta": {"content": content}, "finish_reason": None}]}
        return f"data: {json.dumps(data)}"

    @pytest.mark.asyncio
    async def test_yields_content_chunks(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1", api_key="sk-test")
        service = self._service(config)

        lines = [
            self._sse_line("Hello"),
            self._sse_line(" world"),
            "data: [DONE]",
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._openai.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert chunks == ["Hello", " world"]

    @pytest.mark.asyncio
    async def test_stops_on_done_sentinel(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1")
        service = self._service(config)

        lines = [
            self._sse_line("chunk"),
            "data: [DONE]",
            self._sse_line("after-done"),
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._openai.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert "after-done" not in chunks

    @pytest.mark.asyncio
    async def test_skips_non_data_lines(self):
        """Lines that do not start with 'data: ' must be ignored."""
        config = _make_config("openai", base_url="http://api.openai.test/v1")
        service = self._service(config)

        lines = [
            ": keep-alive",
            "event: message",
            "",
            self._sse_line("real"),
            "data: [DONE]",
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._openai.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert chunks == ["real"]

    @pytest.mark.asyncio
    async def test_skips_blank_lines(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1")
        service = self._service(config)

        lines = [
            "",
            self._sse_line("chunk"),
            "",
            "data: [DONE]",
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._openai.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert chunks == ["chunk"]

    @pytest.mark.asyncio
    async def test_skips_malformed_json_in_data_lines(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1")
        service = self._service(config)

        lines = [
            "data: {broken json",
            self._sse_line("good"),
            "data: [DONE]",
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._openai.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert chunks == ["good"]

    @pytest.mark.asyncio
    async def test_raises_on_non_200_status(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1")
        service = self._service(config)

        client_cm, _, _ = _make_stream_ctx(401, [])

        with patch("httpx.AsyncClient", return_value=client_cm):
            with pytest.raises(ValueError, match="OpenAI returned status 401"):
                await _collect(
                    service._openai.stream_with_history(
                        [{"role": "user", "content": "hi"}], config, "sys"
                    )
                )

    @pytest.mark.asyncio
    async def test_api_key_added_to_authorization_header(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1", api_key="sk-secret")
        service = self._service(config)

        lines = ["data: [DONE]"]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service._openai.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        call_kwargs = mock_client.stream.call_args[1]
        assert call_kwargs["headers"].get("Authorization") == "Bearer sk-secret"

    @pytest.mark.asyncio
    async def test_no_authorization_header_without_api_key(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1", api_key=None)
        service = self._service(config)

        lines = ["data: [DONE]"]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service._openai.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        call_kwargs = mock_client.stream.call_args[1]
        assert "Authorization" not in call_kwargs.get("headers", {})

    @pytest.mark.asyncio
    async def test_stream_flag_is_true(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1")
        service = self._service(config)

        lines = ["data: [DONE]"]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service._openai.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        call_kwargs = mock_client.stream.call_args[1]
        assert call_kwargs["json"]["stream"] is True

    @pytest.mark.asyncio
    async def test_image_data_attached_to_last_user_message(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1")
        service = self._service(config)

        lines = ["data: [DONE]"]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        messages = [{"role": "user", "content": "describe this"}]
        image_data = "base64img"

        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service._openai.stream_with_history(
                    messages, config, "sys", image_data=image_data
                )
            )

        call_kwargs = mock_client.stream.call_args[1]
        api_messages = call_kwargs["json"]["messages"]
        user_msgs = [m for m in api_messages if m["role"] == "user"]
        assert user_msgs, "Expected at least one user message"
        last_user = user_msgs[-1]
        # Content becomes a list when image is attached
        assert isinstance(last_user["content"], list)
        image_parts = [p for p in last_user["content"] if p.get("type") == "image_url"]
        assert image_parts, "Expected image_url part in last user message"
        assert image_data in image_parts[0]["image_url"]["url"]

    @pytest.mark.asyncio
    async def test_skips_delta_without_content(self):
        """Deltas that have no 'content' key (e.g. role-only deltas) should not yield."""
        config = _make_config("openai", base_url="http://api.openai.test/v1")
        service = self._service(config)

        role_delta = json.dumps({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]})
        lines = [
            f"data: {role_delta}",
            self._sse_line("actual"),
            "data: [DONE]",
        ]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service._openai.stream_with_history(
                    [{"role": "user", "content": "hi"}], config, "sys"
                )
            )

        assert chunks == ["actual"]


# ---------------------------------------------------------------------------
# stream_with_history (routing + system message building)
# ---------------------------------------------------------------------------

class TestStreamWithHistory:

    def _ollama_line(self, content: str, done: bool = False) -> str:
        return json.dumps({"message": {"content": content}, "done": done})

    def _sse_line(self, content: str) -> str:
        data = {"choices": [{"delta": {"content": content}, "finish_reason": None}]}
        return f"data: {json.dumps(data)}"

    @pytest.mark.asyncio
    async def test_routes_to_ollama(self):
        config = _make_config("ollama")
        repo = _make_mock_repo(config)
        service = LLMGateway(llm_repository=repo)

        lines = [self._ollama_line("hi", done=True)]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service.stream_with_history(
                    [{"role": "user", "content": "hello"}], llm_id="cfg-1"
                )
            )

        assert chunks == ["hi"]

    @pytest.mark.asyncio
    async def test_routes_to_openai(self):
        config = _make_config("openai", base_url="http://api.openai.test/v1")
        repo = _make_mock_repo(config)
        service = LLMGateway(llm_repository=repo)

        lines = [self._sse_line("hello"), "data: [DONE]"]
        client_cm, _, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            chunks = await _collect_tokens(
                service.stream_with_history(
                    [{"role": "user", "content": "hello"}], llm_id="cfg-1"
                )
            )

        assert chunks == ["hello"]

    @pytest.mark.asyncio
    async def test_raises_when_config_not_found(self):
        repo = _make_mock_repo(None)  # config not found
        service = LLMGateway(llm_repository=repo)

        with pytest.raises(ValueError, match="not found"):
            await _collect(
                service.stream_with_history(
                    [{"role": "user", "content": "hi"}], llm_id="missing-id"
                )
            )

    @pytest.mark.asyncio
    async def test_raises_when_config_disabled(self):
        config = _make_config("ollama", enabled=False)
        repo = _make_mock_repo(config)
        service = LLMGateway(llm_repository=repo)

        with pytest.raises(ValueError, match="disabled"):
            await _collect(
                service.stream_with_history(
                    [{"role": "user", "content": "hi"}], llm_id="cfg-1"
                )
            )

    @pytest.mark.asyncio
    async def test_raises_for_unsupported_provider_type(self):
        config = _make_config("anthropic")  # unknown type
        repo = _make_mock_repo(config)
        service = LLMGateway(llm_repository=repo)

        with pytest.raises(ValueError, match="Unsupported LLM type"):
            await _collect(
                service.stream_with_history(
                    [{"role": "user", "content": "hi"}], llm_id="cfg-1"
                )
            )

    @pytest.mark.asyncio
    async def test_custom_system_message_overrides_config(self):
        """custom_system_message must be forwarded verbatim to the underlying stream method."""
        config = _make_config("ollama")
        repo = _make_mock_repo(config)
        service = LLMGateway(llm_repository=repo)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        custom_msg = "Be a pirate."
        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service.stream_with_history(
                    [{"role": "user", "content": "hi"}],
                    llm_id="cfg-1",
                    custom_system_message=custom_msg,
                )
            )

        call_kwargs = mock_client.stream.call_args[1]
        system_msgs = [m for m in call_kwargs["json"]["messages"] if m["role"] == "system"]
        assert system_msgs, "Expected a system message in the Ollama payload"
        assert system_msgs[0]["content"] == custom_msg

    @pytest.mark.asyncio
    async def test_mode_uses_config_system_message(self):
        """mode='generation' with no custom message should use the config's default system message."""
        config = _make_config("ollama")
        repo = _make_mock_repo(config)
        service = LLMGateway(llm_repository=repo)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            await _collect(
                service.stream_with_history(
                    [{"role": "user", "content": "improve my prompt"}],
                    llm_id="cfg-1",
                    mode="generation",
                )
            )

        call_kwargs = mock_client.stream.call_args[1]
        system_msgs = [m for m in call_kwargs["json"]["messages"] if m["role"] == "system"]
        assert system_msgs, "Expected a system message in the Ollama payload"
        assert system_msgs[0]["content"] == config.system_message


# ---------------------------------------------------------------------------
# Connection / network error propagation
# ---------------------------------------------------------------------------

class TestStreamingNetworkErrors:
    """Verify that low-level httpx errors bubble up wrapped in ValueError."""

    @pytest.mark.asyncio
    async def test_ollama_connection_error_propagates(self):
        import httpx

        config = _make_config("ollama")
        repo = _make_mock_repo(config)
        service = LLMGateway(llm_repository=repo)

        # Simulate the outer AsyncClient raising a connection error
        mock_client_cm = AsyncMock()
        mock_client_cm.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client_cm):
            with pytest.raises(Exception):
                await _collect(
                    service._ollama.stream_with_history(
                        [{"role": "user", "content": "hi"}], config, "sys"
                    )
                )

    @pytest.mark.asyncio
    async def test_openai_connection_error_propagates(self):
        import httpx

        config = _make_config("openai", base_url="http://api.openai.test/v1")
        repo = _make_mock_repo(config)
        service = LLMGateway(llm_repository=repo)

        mock_client_cm = AsyncMock()
        mock_client_cm.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client_cm):
            with pytest.raises(Exception):
                await _collect(
                    service._openai.stream_with_history(
                        [{"role": "user", "content": "hi"}], config, "sys"
                    )
                )
