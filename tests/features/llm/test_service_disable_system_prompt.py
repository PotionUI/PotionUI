"""Tests for LLMGateway disable_system_prompt feature.

Covers:
- generate_with_config_id: System prompt disabled results in empty system_message
- generate_with_history: System prompt disabled results in empty system_message
- generate_with_history: Custom system message still works even when disabled
- stream_with_history: System prompt disabled results in empty system_message
- generate_with_tools: System prompt disabled results in empty system_message
- generate_with_tools: Custom system message still works even when disabled
- stream_with_tools: System prompt disabled results in empty system_message
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
        disable_system_prompt=False,
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
    """Create an LLMGateway with a mocked repository."""
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
    """Collect only token content strings from a dict-yielding stream."""
    result = []
    async for item in async_gen:
        if isinstance(item, dict) and item.get("type") == "token":
            result.append(item["content"])
    return result


# ---------------------------------------------------------------------------
# Async mock context-manager helpers
# ---------------------------------------------------------------------------

def _make_stream_ctx(status_code: int, lines: list[str]):
    """Build the nested async context managers that httpx.AsyncClient.stream() returns."""
    # Inner: the streaming response object
    async def _aiter_lines():
        for line in lines:
            yield line

    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.aiter_lines = _aiter_lines
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


def _make_post_ctx(status_code: int, response_data: dict):
    """Build async context manager for httpx.AsyncClient.post() calls."""
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


# ---------------------------------------------------------------------------
# generate_with_config_id
# ---------------------------------------------------------------------------

class TestGenerateWithConfigIdDisableSystemPrompt:

    @pytest.mark.asyncio
    async def test_disable_system_prompt_uses_empty_string(self):
        """When disable_system_prompt=True, generate_with_config_id passes empty string as system_message."""
        config = _make_config("ollama", disable_system_prompt=True, system_message="You are helpful.")
        service = _make_service(config)

        # Mock the response
        response_data = {
            "message": {"content": "Response text"},
            "eval_count": 10,
            "prompt_eval_count": 5
        }
        client_cm, mock_client, _ = _make_post_ctx(200, response_data)

        with patch("httpx.AsyncClient", return_value=client_cm):
            await service.generate_with_config_id("test prompt", llm_id="cfg-1")

        # Verify the post was called with empty system message
        call_kwargs = mock_client.post.call_args[1]
        assert "json" in call_kwargs
        # For vision endpoint, system message is in the 'system' field
        # For non-vision, it's in messages array
        # Since no image_data, this should use /api/generate endpoint with system field
        payload = call_kwargs["json"]

        # Check if system message is empty (could be in 'system' field or messages array)
        if "system" in payload:
            assert payload["system"] == ""
        else:
            # Check messages array for system message
            system_msgs = [m for m in payload.get("messages", []) if m["role"] == "system"]
            if system_msgs:
                assert system_msgs[0]["content"] == ""



# ---------------------------------------------------------------------------
# generate_with_history
# ---------------------------------------------------------------------------

class TestGenerateWithHistoryDisableSystemPrompt:

    @pytest.mark.asyncio
    async def test_disable_system_prompt_uses_empty_string(self):
        """When disable_system_prompt=True, generate_with_history passes empty string."""
        config = _make_config("ollama", disable_system_prompt=True)
        service = _make_service(config)

        response_data = {
            "message": {"content": "Response"},
            "eval_count": 10,
            "prompt_eval_count": 5
        }
        client_cm, mock_client, _ = _make_post_ctx(200, response_data)

        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await service.generate_with_history(messages, llm_id="cfg-1")

        call_kwargs = mock_client.post.call_args[1]
        payload = call_kwargs["json"]

        # Check messages array - system message should be empty or not present
        system_msgs = [m for m in payload.get("messages", []) if m["role"] == "system"]
        # Either no system message or empty content
        assert len(system_msgs) == 0 or system_msgs[0]["content"] == ""

    @pytest.mark.asyncio
    async def test_disable_system_prompt_custom_still_works(self):
        """When disable_system_prompt=True BUT custom_system_message provided, custom is used."""
        config = _make_config("ollama", disable_system_prompt=True, system_message="Default")
        service = _make_service(config)

        response_data = {
            "message": {"content": "Response"},
            "eval_count": 10,
            "prompt_eval_count": 5
        }
        client_cm, mock_client, _ = _make_post_ctx(200, response_data)

        custom_msg = "You are a pirate."
        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await service.generate_with_history(messages, llm_id="cfg-1", custom_system_message=custom_msg)

        call_kwargs = mock_client.post.call_args[1]
        payload = call_kwargs["json"]

        # Custom system message should be used
        system_msgs = [m for m in payload.get("messages", []) if m["role"] == "system"]
        assert len(system_msgs) > 0
        assert system_msgs[0]["content"] == custom_msg


# ---------------------------------------------------------------------------
# stream_with_history
# ---------------------------------------------------------------------------

class TestStreamWithHistoryDisableSystemPrompt:

    def _ollama_line(self, content: str, done: bool = False) -> str:
        return json.dumps({"message": {"content": content}, "done": done})

    @pytest.mark.asyncio
    async def test_disable_system_prompt_uses_empty_string(self):
        """When disable_system_prompt=True, stream_with_history passes empty string."""
        config = _make_config("ollama", disable_system_prompt=True)
        service = _make_service(config)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await _collect(service.stream_with_history(messages, llm_id="cfg-1"))

        call_kwargs = mock_client.stream.call_args[1]
        payload = call_kwargs["json"]

        # System message should be empty or not present
        system_msgs = [m for m in payload.get("messages", []) if m["role"] == "system"]
        assert len(system_msgs) == 0 or system_msgs[0]["content"] == ""

    @pytest.mark.asyncio
    async def test_disable_system_prompt_custom_still_works(self):
        """When disable_system_prompt=True BUT custom_system_message provided, custom is used."""
        config = _make_config("ollama", disable_system_prompt=True)
        service = _make_service(config)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        custom_msg = "You are a pirate."
        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await _collect(service.stream_with_history(messages, llm_id="cfg-1", custom_system_message=custom_msg))

        call_kwargs = mock_client.stream.call_args[1]
        payload = call_kwargs["json"]

        # Custom system message should be used
        system_msgs = [m for m in payload.get("messages", []) if m["role"] == "system"]
        assert len(system_msgs) > 0
        assert system_msgs[0]["content"] == custom_msg


# ---------------------------------------------------------------------------
# generate_with_tools
# ---------------------------------------------------------------------------

class TestGenerateWithToolsDisableSystemPrompt:

    @pytest.mark.asyncio
    async def test_disable_system_prompt_uses_empty_string(self):
        """When disable_system_prompt=True, generate_with_tools passes empty string."""
        config = _make_config("ollama", disable_system_prompt=True)
        service = _make_service(config)

        response_data = {
            "message": {"content": "Response"},
            "eval_count": 10,
            "prompt_eval_count": 5
        }
        client_cm, mock_client, _ = _make_post_ctx(200, response_data)

        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await service.generate_with_tools(messages, llm_id="cfg-1")

        call_kwargs = mock_client.post.call_args[1]
        payload = call_kwargs["json"]

        # System message should be empty or not present
        system_msgs = [m for m in payload.get("messages", []) if m["role"] == "system"]
        assert len(system_msgs) == 0 or system_msgs[0]["content"] == ""

    @pytest.mark.asyncio
    async def test_disable_system_prompt_custom_still_works(self):
        """When disable_system_prompt=True BUT custom_system_message provided, custom is used."""
        config = _make_config("ollama", disable_system_prompt=True)
        service = _make_service(config)

        response_data = {
            "message": {"content": "Response"},
            "eval_count": 10,
            "prompt_eval_count": 5
        }
        client_cm, mock_client, _ = _make_post_ctx(200, response_data)

        custom_msg = "You are a tool executor."
        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await service.generate_with_tools(messages, llm_id="cfg-1", custom_system_message=custom_msg)

        call_kwargs = mock_client.post.call_args[1]
        payload = call_kwargs["json"]

        # Custom system message should be used
        system_msgs = [m for m in payload.get("messages", []) if m["role"] == "system"]
        assert len(system_msgs) > 0
        assert system_msgs[0]["content"] == custom_msg


# ---------------------------------------------------------------------------
# stream_with_tools
# ---------------------------------------------------------------------------

class TestStreamWithToolsDisableSystemPrompt:

    def _ollama_line(self, content: str, done: bool = False) -> str:
        return json.dumps({"message": {"content": content}, "done": done})

    @pytest.mark.asyncio
    async def test_disable_system_prompt_uses_empty_string(self):
        """When disable_system_prompt=True, stream_with_tools passes empty string."""
        config = _make_config("ollama", disable_system_prompt=True)
        service = _make_service(config)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await _collect(service.stream_with_tools(messages, llm_id="cfg-1"))

        call_kwargs = mock_client.stream.call_args[1]
        payload = call_kwargs["json"]

        # System message should be empty or not present
        system_msgs = [m for m in payload.get("messages", []) if m["role"] == "system"]
        assert len(system_msgs) == 0 or system_msgs[0]["content"] == ""

    @pytest.mark.asyncio
    async def test_disable_system_prompt_custom_still_works(self):
        """When disable_system_prompt=True BUT custom_system_message provided, custom is used."""
        config = _make_config("ollama", disable_system_prompt=True)
        service = _make_service(config)

        lines = [self._ollama_line("ok", done=True)]
        client_cm, mock_client, _ = _make_stream_ctx(200, lines)

        custom_msg = "You are a streaming tool executor."
        with patch("httpx.AsyncClient", return_value=client_cm):
            messages = [{"role": "user", "content": "hello"}]
            await _collect(service.stream_with_tools(messages, llm_id="cfg-1", custom_system_message=custom_msg))

        call_kwargs = mock_client.stream.call_args[1]
        payload = call_kwargs["json"]

        # Custom system message should be used
        system_msgs = [m for m in payload.get("messages", []) if m["role"] == "system"]
        assert len(system_msgs) > 0
        assert system_msgs[0]["content"] == custom_msg

