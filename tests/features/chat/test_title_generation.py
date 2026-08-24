"""Tests for ChatTitleGenerator and the title SSE event in send_message_stream."""

import pytest
from unittest.mock import Mock, AsyncMock
from typing import AsyncGenerator, List

from src.features.chat.manager import ChatManager
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.chat.title_generator import ChatTitleGenerator


def _mode_registry() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


def _message(role: str, content: str) -> Mock:
    msg = Mock()
    msg.role = role
    msg.content = content
    return msg


def _session(title_generated: bool = False, llm_config_id: str = "llm-123") -> Mock:
    session = Mock()
    session.id = "session-123"
    session.title_generated = title_generated
    session.llm_config_id = llm_config_id
    return session


async def collect_stream(gen: AsyncGenerator) -> List[dict]:
    events = []
    async for event in gen:
        events.append(event)
    return events


class TestShouldGenerate:
    def setup_method(self):
        self.generator = ChatTitleGenerator(Mock(), Mock())

    def test_true_within_message_window(self):
        assert self.generator.should_generate(_session(), 2) is True
        assert self.generator.should_generate(_session(), 6) is True

    def test_false_before_first_exchange(self):
        assert self.generator.should_generate(_session(), 0) is False
        assert self.generator.should_generate(_session(), 1) is False

    def test_false_after_retry_budget(self):
        assert self.generator.should_generate(_session(), 7) is False

    def test_false_when_already_titled(self):
        assert self.generator.should_generate(_session(title_generated=True), 2) is False


class TestSanitize:
    def test_strips_surrounding_quotes(self):
        assert ChatTitleGenerator.sanitize('"Sunset Portrait Ideas"') == "Sunset Portrait Ideas"
        assert ChatTitleGenerator.sanitize("'Anime Style Tips'") == "Anime Style Tips"
        assert ChatTitleGenerator.sanitize("“Curly Quotes Title”") == "Curly Quotes Title"

    def test_strips_trailing_punctuation(self):
        assert ChatTitleGenerator.sanitize("Improving LoRA prompts.") == "Improving LoRA prompts"
        assert ChatTitleGenerator.sanitize("Better lighting!") == "Better lighting"

    def test_uses_first_nonempty_line_and_collapses_whitespace(self):
        assert ChatTitleGenerator.sanitize("\n\n  Two   Word\tTitle \nsecond line") == "Two Word Title"

    def test_caps_length(self):
        long = "word " * 40
        result = ChatTitleGenerator.sanitize(long)
        assert len(result) <= 80

    def test_empty_and_none_return_none(self):
        assert ChatTitleGenerator.sanitize(None) is None
        assert ChatTitleGenerator.sanitize("") is None
        assert ChatTitleGenerator.sanitize('"..."') is None


class TestGenerate:
    def setup_method(self):
        self.mock_llm = Mock()
        self.mock_repo = Mock()
        self.generator = ChatTitleGenerator(self.mock_llm, self.mock_repo)

        self.mock_repo.get_session.return_value = _session()
        self.mock_repo.get_messages.return_value = [
            _message("user", "how do I prompt this lora?"),
            _message("assistant", "Use its trigger words..."),
        ]
        self.mock_repo.set_session_title.return_value = _session(title_generated=True)

        response = Mock()
        response.content = '"LoRA Prompting Help"'
        self.mock_llm.generate_with_history = AsyncMock(return_value=response)

    @pytest.mark.asyncio
    async def test_happy_path_persists_sanitized_title(self):
        title = await self.generator.generate("session-123")

        assert title == "LoRA Prompting Help"
        self.mock_repo.set_session_title.assert_called_once_with("session-123", "LoRA Prompting Help")
        call_kwargs = self.mock_llm.generate_with_history.call_args.kwargs
        assert call_kwargs["llm_id"] == "llm-123"
        assert call_kwargs["options_override"]["max_tokens"] == 24
        assert call_kwargs["options_override"]["think"] is False

    @pytest.mark.asyncio
    async def test_missing_session_returns_none(self):
        self.mock_repo.get_session.return_value = None
        assert await self.generator.generate("session-123") is None
        self.mock_llm.generate_with_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_titled_returns_none(self):
        self.mock_repo.get_session.return_value = _session(title_generated=True)
        assert await self.generator.generate("session-123") is None
        self.mock_llm.generate_with_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_assistant_message_returns_none(self):
        self.mock_repo.get_messages.return_value = [_message("user", "hello")]
        assert await self.generator.generate("session-123") is None
        self.mock_llm.generate_with_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_leaves_title_unset(self):
        self.mock_llm.generate_with_history = AsyncMock(side_effect=RuntimeError("provider down"))
        assert await self.generator.generate("session-123") is None
        self.mock_repo.set_session_title.assert_not_called()

    @pytest.mark.asyncio
    async def test_unusable_llm_output_leaves_title_unset(self):
        response = Mock()
        response.content = "..."
        self.mock_llm.generate_with_history = AsyncMock(return_value=response)
        assert await self.generator.generate("session-123") is None
        self.mock_repo.set_session_title.assert_not_called()


class TestStreamTitleEvent:
    """send_message_stream yields a title event after done when a title is generated."""

    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_llm = Mock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()

        no_block_ctx = Mock()
        no_block_ctx.data = {}
        self.mock_plugins.execute_hook.return_value = (no_block_ctx, [])
        self.mock_processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

        session = _session()
        session.user_id = "user-123"
        session.status = "active"
        session.mode = "generation"
        session.metadata = {"enabled_tools": []}

        saved_message = Mock()
        saved_message.id = "msg-1"
        saved_message.model_dump.return_value = {"id": "msg-1"}

        self.mock_repo.get_session.return_value = session
        self.mock_repo.add_message.return_value = saved_message
        self.mock_repo.get_conversation_history.return_value = [{"role": "user", "content": "hi"}]
        self.mock_repo.count_messages.return_value = 2
        self.mock_repo.get_messages.return_value = [
            _message("user", "hi"),
            _message("assistant", "hello"),
        ]
        self.mock_repo.set_session_title.return_value = session

        async def _stream(**kwargs):
            yield {"type": "token", "content": "hello"}
            yield {"type": "usage", "tokens_used": 1, "prompt_tokens": 1, "completion_tokens": 1}

        self.mock_llm.stream_with_history = Mock(side_effect=lambda **kwargs: _stream(**kwargs))
        title_response = Mock()
        title_response.content = "Friendly Greeting Chat"
        self.mock_llm.generate_with_history = AsyncMock(return_value=title_response)

    @pytest.mark.asyncio
    async def test_title_event_yielded_after_done(self):
        events = await collect_stream(
            self.manager.send_message_stream("session-123", "user-123", "hi")
        )

        event_names = [e["event"] for e in events]
        assert "done" in event_names and "title" in event_names
        assert event_names.index("title") > event_names.index("done")
        title_event = next(e for e in events if e["event"] == "title")
        assert title_event["data"] == {"session_id": "session-123", "name": "Friendly Greeting Chat"}
        self.mock_repo.set_session_title.assert_called_once_with("session-123", "Friendly Greeting Chat")

    @pytest.mark.asyncio
    async def test_no_title_event_when_already_titled(self):
        self.mock_repo.get_session.return_value.title_generated = True

        events = await collect_stream(
            self.manager.send_message_stream("session-123", "user-123", "hi")
        )

        event_names = [e["event"] for e in events]
        assert "done" in event_names
        assert "title" not in event_names

    @pytest.mark.asyncio
    async def test_title_failure_does_not_break_stream(self):
        self.mock_llm.generate_with_history = AsyncMock(side_effect=RuntimeError("boom"))

        events = await collect_stream(
            self.manager.send_message_stream("session-123", "user-123", "hi")
        )

        event_names = [e["event"] for e in events]
        assert "done" in event_names
        assert "title" not in event_names
        assert "error" not in event_names
