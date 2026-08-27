"""Tests for ChatRuntime.send_message_stream method."""

import pytest
from unittest.mock import ANY, Mock, MagicMock, AsyncMock, patch, call
from typing import AsyncGenerator, List

from src.features.chat.runtime import ChatRuntime

from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.chat.reply_contract import REPLY_CONTRACT_REMINDER


def _mode_registry() -> ChatModeRegistry:
    """Real mode registry with the builtin generation mode (no settings)."""
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry
from src.features.chat.exceptions import (
    SessionNotFoundException,
    AccessDeniedException,
    SessionClosedException,
    InvalidLLMConfigException,
    MessageCreationFailedException,
)
from src.features.chat.dto import MessageResponse
from src.features.llm.tools.base import ToolExecution, ToolResult
from src.features.chat.hooks import CHAT_MESSAGE_HOOKS
from src.features.chat.hooks import CHAT_RESPONSE_HOOKS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_message_response(
    msg_id: str,
    session_id: str = "session-123",
    role: str = "assistant",
    content: str = "response",
) -> MessageResponse:
    """Build a minimal MessageResponse for use in tests."""
    return MessageResponse(
        id=msg_id,
        session_id=session_id,
        role=role,
        content=content,
    )


async def collect_stream(gen: AsyncGenerator) -> List[dict]:
    """Collect all events from an async generator into a list."""
    events = []
    async for event in gen:
        events.append(event)
    return events


def make_async_gen(chunks: List[str]):
    """Return an async generator that yields dicts matching stream_with_history format.

    Each chunk becomes a {"type": "token", "content": chunk} event, followed
    by a final {"type": "usage", ...} event with null token counts.
    """
    async def _gen():
        for chunk in chunks:
            yield {"type": "token", "content": chunk}
        yield {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None}
    return _gen()


# ---------------------------------------------------------------------------
# Fixtures / base setup
# ---------------------------------------------------------------------------

class BaseStreamingTest:
    """Shared setup for all send_message_stream test classes."""

    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_llm = Mock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()

        # Default plugin context: no blocking, empty modifications
        no_block_ctx = Mock()
        no_block_ctx.data = {}
        self.mock_plugins.execute_hook.return_value = (no_block_ctx, [])

        # Default processor: pass content through unchanged
        self.mock_processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        self.manager = ChatRuntime(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    def _make_active_session(
        self,
        user_id: str = "user-123",
        llm_config_id: str = "llm-123",
        mode: str = "generation",
        metadata: dict = None,
    ) -> Mock:
        session = Mock()
        session.id = "session-123"
        session.user_id = user_id
        session.status = "active"
        session.llm_config_id = llm_config_id
        session.mode = mode
        session.metadata = metadata
        return session


# ---------------------------------------------------------------------------
# Validation / pre-yield error tests
# ---------------------------------------------------------------------------

class TestSendMessageStreamValidation(BaseStreamingTest):
    """Tests for errors that should be raised before the first yield."""

    @pytest.mark.asyncio
    async def test_raises_session_not_found(self):
        """SessionNotFoundException raised synchronously before first yield."""
        self.mock_repo.get_session.return_value = None

        gen = self.manager.send_message_stream(
            session_id="missing",
            user_id="user-123",
            content="hello",
        )

        with pytest.raises(SessionNotFoundException):
            async for _ in gen:
                pass

    @pytest.mark.asyncio
    async def test_raises_access_denied(self):
        """AccessDeniedException raised for wrong user before first yield."""
        session = self._make_active_session(user_id="owner")
        self.mock_repo.get_session.return_value = session

        gen = self.manager.send_message_stream(
            session_id="session-123",
            user_id="intruder",
            content="hello",
        )

        with pytest.raises(AccessDeniedException):
            async for _ in gen:
                pass

    @pytest.mark.asyncio
    async def test_raises_session_closed(self):
        """SessionClosedException raised for non-active session before first yield."""
        session = self._make_active_session()
        session.status = "accepted"
        self.mock_repo.get_session.return_value = session

        gen = self.manager.send_message_stream(
            session_id="session-123",
            user_id="user-123",
            content="hello",
        )

        with pytest.raises(SessionClosedException):
            async for _ in gen:
                pass

    @pytest.mark.asyncio
    async def test_raises_invalid_llm_config(self):
        """InvalidLLMConfigException raised when llm_config_id is None before first yield."""
        session = self._make_active_session(llm_config_id=None)
        self.mock_repo.get_session.return_value = session

        gen = self.manager.send_message_stream(
            session_id="session-123",
            user_id="user-123",
            content="hello",
        )

        with pytest.raises(InvalidLLMConfigException):
            async for _ in gen:
                pass

    @pytest.mark.asyncio
    async def test_raises_when_hook_blocks_message(self):
        """MessageCreationFailedException raised when before_send hook blocks."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session

        blocked_ctx = Mock()
        blocked_ctx.data = {"blocked": True, "block_reason": "spam detected"}
        self.mock_plugins.execute_hook.return_value = (blocked_ctx, [])

        gen = self.manager.send_message_stream(
            session_id="session-123",
            user_id="user-123",
            content="hello",
        )

        with pytest.raises(MessageCreationFailedException) as exc_info:
            async for _ in gen:
                pass

        assert "spam detected" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_raises_when_user_message_save_fails(self):
        """MessageCreationFailedException raised when add_message returns None for user msg."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.add_message.return_value = None

        gen = self.manager.send_message_stream(
            session_id="session-123",
            user_id="user-123",
            content="hello",
        )

        with pytest.raises(MessageCreationFailedException):
            async for _ in gen:
                pass


# ---------------------------------------------------------------------------
# Normal (non-tools) streaming path
# ---------------------------------------------------------------------------

class TestSendMessageStreamNormalPath(BaseStreamingTest):
    """Tests for the standard streaming path (tools disabled)."""

    def _setup_basic_stream(self, chunks: List[str]):
        """Configure mocks for a simple streaming scenario."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user", content="hello")
        assistant_msg = make_message_response("msg-asst", role="assistant", content="".join(chunks))
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(chunks))

        return user_msg, assistant_msg

    @pytest.mark.asyncio
    async def test_event_sequence_message_created_tokens_done(self):
        """Should yield status(loading_memory), message_created, tokens, then done."""
        chunks = ["Hello", " ", "world"]
        user_msg, assistant_msg = self._setup_basic_stream(chunks)

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        event_types = [e["event"] for e in events]
        # loading_memory status always brackets the pre-token block, before message_created
        assert event_types[:1] == ["status"]
        assert "message_created" in event_types
        assert event_types.index("message_created") < event_types.index("token")
        assert event_types[-1] == "done"
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) == len(chunks)

    @pytest.mark.asyncio
    async def test_message_created_contains_user_message_id(self):
        """message_created event should carry the user_message_id."""
        user_msg, _ = self._setup_basic_stream(["hi"])

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        mc_event = next(e for e in events if e["event"] == "message_created")
        assert mc_event["data"]["user_message_id"] == user_msg.id

    @pytest.mark.asyncio
    async def test_token_events_carry_correct_content(self):
        """Each token event should contain the exact chunk text."""
        chunks = ["foo", "bar", "baz"]
        self._setup_basic_stream(chunks)

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        token_contents = [e["data"]["content"] for e in events if e["event"] == "token"]
        assert token_contents == chunks

    @pytest.mark.asyncio
    async def test_done_event_contains_assistant_and_user_messages(self):
        """done event data should include assistant_message and user_message."""
        user_msg, assistant_msg = self._setup_basic_stream(["answer"])

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        done_event = next(e for e in events if e["event"] == "done")
        assert "assistant_message" in done_event["data"]
        assert "user_message" in done_event["data"]
        assert done_event["data"]["assistant_message"]["id"] == assistant_msg.id
        assert done_event["data"]["user_message"]["id"] == user_msg.id

    @pytest.mark.asyncio
    async def test_no_error_event_on_success(self):
        """No error event should appear in a successful stream."""
        self._setup_basic_stream(["chunk"])

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 0

    @pytest.mark.asyncio
    async def test_full_content_is_concatenation_of_chunks(self):
        """The assistant message saved to DB should contain all chunks joined."""
        chunks = ["one", " ", "two"]
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user", content="hello")
        # Processor returns what we send; assistant message will be saved with joined chunks
        expected_content = "".join(chunks)
        assistant_msg = make_message_response("msg-asst", role="assistant", content=expected_content)
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(chunks))

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        # Second call to add_message is for the assistant — verify content
        second_call_kwargs = self.mock_repo.add_message.call_args_list[1][1]
        assert second_call_kwargs["content"] == expected_content
        assert second_call_kwargs["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_stream_with_history_called_correctly(self):
        """stream_with_history should receive correct arguments."""
        session = self._make_active_session(llm_config_id="llm-abc", mode="generation")
        self.mock_repo.get_session.return_value = session
        history = [{"role": "user", "content": "hi"}]
        self.mock_repo.get_conversation_history.return_value = history

        user_msg = make_message_response("msg-user", role="user", content="hello")
        assistant_msg = make_message_response("msg-asst")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["ok"]))

        with patch("src.features.chat.conversation.convert_image_to_base64", return_value=None):
            await collect_stream(
                self.manager.send_message_stream(
                    session_id="session-123",
                    user_id="user-123",
                    content="hello",
                )
            )

        self.mock_llm.stream_with_history.assert_called_once_with(
            messages=history,
            llm_id="llm-abc",
            image_data=None,
            custom_system_message=ANY,
            mode="generation",
            options_override=None,
        )

    @pytest.mark.asyncio
    async def test_image_data_is_converted_to_base64(self):
        """Image path should be converted to base64 before LLM call."""
        self._setup_basic_stream(["ok"])

        with patch("src.features.chat.conversation.convert_image_to_base64", return_value="b64img") as mock_convert:
            await collect_stream(
                self.manager.send_message_stream(
                    session_id="session-123",
                    user_id="user-123",
                    content="hello",
                    image_data="/path/to/img.jpg",
                )
            )

        mock_convert.assert_called_once_with("/path/to/img.jpg")
        call_kwargs = self.mock_llm.stream_with_history.call_args[1]
        assert call_kwargs["image_data"] == "b64img"

    @pytest.mark.asyncio
    async def test_empty_token_stream(self):
        """An empty stream should still emit message_created and done."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user", content="hello")
        assistant_msg = make_message_response("msg-asst", content="")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen([]))

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        event_types = [e["event"] for e in events]
        assert "message_created" in event_types
        assert "done" in event_types
        assert "token" not in event_types


# ---------------------------------------------------------------------------
# DB persistence tests
# ---------------------------------------------------------------------------

class TestSendMessageStreamPersistence(BaseStreamingTest):
    """Tests that verify correct DB persistence after streaming completes."""

    @pytest.mark.asyncio
    async def test_user_message_persisted_before_streaming(self):
        """User message should be saved to DB before the stream begins."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user", content="hello")
        assistant_msg = make_message_response("msg-asst")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["ok"]))

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        # First add_message call must be for the user role
        first_call = self.mock_repo.add_message.call_args_list[0][1]
        assert first_call["role"] == "user"
        assert first_call["content"] == "hello"
        assert first_call["session_id"] == "session-123"

    @pytest.mark.asyncio
    async def test_assistant_message_persisted_after_stream(self):
        """Assistant message should be saved to DB after all tokens are collected."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user", content="hello")
        assistant_msg = make_message_response("msg-asst", content="full response")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["full response"]))

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        assert self.mock_repo.add_message.call_count == 2
        second_call = self.mock_repo.add_message.call_args_list[1][1]
        assert second_call["role"] == "assistant"
        assert second_call["session_id"] == "session-123"

    @pytest.mark.asyncio
    async def test_add_message_called_twice_total(self):
        """Exactly two add_message calls should be made: user then assistant."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        assistant_msg = make_message_response("msg-asst")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["chunk"]))

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        assert self.mock_repo.add_message.call_count == 2

    @pytest.mark.asyncio
    async def test_response_processor_called_with_full_content(self):
        """ResponseProcessor.process should receive the fully assembled content."""
        chunks = ["part1", "part2", "part3"]
        expected = "part1part2part3"

        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        assistant_msg = make_message_response("msg-asst", content=expected)
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(chunks))

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        self.mock_processor.process.assert_called_once_with(
            expected,
            mode=session.mode,
        )

    @pytest.mark.asyncio
    async def test_error_event_when_assistant_message_save_fails(self):
        """Should yield error event (not raise) when assistant message save returns None."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        # Second add_message returns None → assistant save fails
        self.mock_repo.add_message.side_effect = [user_msg, None]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["chunk"]))

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["data"]["error"] == "message_creation_failed"


# ---------------------------------------------------------------------------
# Hook execution tests
# ---------------------------------------------------------------------------

class TestSendMessageStreamHooks(BaseStreamingTest):
    """Tests that verify hooks are executed at the correct points."""

    def _setup_successful_stream(self):
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        assistant_msg = make_message_response("msg-asst")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["ok"]))
        return session

    @pytest.mark.asyncio
    async def test_before_send_hook_executed(self):
        """CHAT_MESSAGE_BEFORE_SEND hook should be called."""
        self._setup_successful_stream()

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        hook_calls = [str(c) for c in self.mock_plugins.execute_hook.call_args_list]
        assert any(CHAT_MESSAGE_HOOKS.before_send in c for c in hook_calls)

    @pytest.mark.asyncio
    async def test_before_generate_hook_executed(self):
        """CHAT_RESPONSE_BEFORE_GENERATE hook should be called."""
        self._setup_successful_stream()

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        hook_calls = [str(c) for c in self.mock_plugins.execute_hook.call_args_list]
        assert any(CHAT_RESPONSE_HOOKS.before_generate in c for c in hook_calls)

    @pytest.mark.asyncio
    async def test_after_save_hook_executed_after_streaming(self):
        """CHAT_RESPONSE_AFTER_SAVE hook should be called after assistant message is saved."""
        self._setup_successful_stream()

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        hook_calls = [str(c) for c in self.mock_plugins.execute_hook.call_args_list]
        assert any(CHAT_RESPONSE_HOOKS.after_save in c for c in hook_calls)

    @pytest.mark.asyncio
    async def test_after_send_hook_executed_after_streaming(self):
        """CHAT_MESSAGE_AFTER_SEND hook should be called after full exchange."""
        self._setup_successful_stream()

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        hook_calls = [str(c) for c in self.mock_plugins.execute_hook.call_args_list]
        assert any(CHAT_MESSAGE_HOOKS.after_send in c for c in hook_calls)

    @pytest.mark.asyncio
    async def test_hooks_not_executed_when_assistant_save_fails(self):
        """after_save and after_send hooks should NOT be called if assistant save fails."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        self.mock_repo.add_message.side_effect = [user_msg, None]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["chunk"]))

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        hook_calls = [str(c) for c in self.mock_plugins.execute_hook.call_args_list]
        assert not any(CHAT_RESPONSE_HOOKS.after_save in c for c in hook_calls)
        assert not any(CHAT_MESSAGE_HOOKS.after_send in c for c in hook_calls)

    @pytest.mark.asyncio
    async def test_before_send_hook_can_modify_content(self):
        """CHAT_MESSAGE_BEFORE_SEND hook should be able to modify the content."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user", content="modified content")
        assistant_msg = make_message_response("msg-asst")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["ok"]))

        # Hook modifies content
        modified_ctx = Mock()
        modified_ctx.data = {"content": "modified content"}
        self.mock_plugins.execute_hook.return_value = (modified_ctx, [])

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="original content",
            )
        )

        # The user message should be saved with the modified content from the hook
        first_call = self.mock_repo.add_message.call_args_list[0][1]
        assert first_call["content"] == "modified content"

    @pytest.mark.asyncio
    async def test_before_generate_hook_can_modify_conversation_history(self):
        """CHAT_RESPONSE_BEFORE_GENERATE hook should be able to inject history changes."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        original_history = [{"role": "user", "content": "hi"}]
        modified_history = [{"role": "system", "content": "injected"}, {"role": "user", "content": "hi"}]
        self.mock_repo.get_conversation_history.return_value = original_history

        user_msg = make_message_response("msg-user", role="user")
        assistant_msg = make_message_response("msg-asst")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["ok"]))

        call_count = [0]

        def dynamic_hook(hook_name, initial_data):
            ctx = Mock()
            # On the second hook call (before_generate), inject modified history
            if call_count[0] == 1:
                ctx.data = {"conversation_history": modified_history}
            else:
                ctx.data = {}
            call_count[0] += 1
            return (ctx, [])

        self.mock_plugins.execute_hook.side_effect = dynamic_hook

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hi",
            )
        )

        call_kwargs = self.mock_llm.stream_with_history.call_args[1]
        assert call_kwargs["messages"] == modified_history


# ---------------------------------------------------------------------------
# Tools-enabled path
# ---------------------------------------------------------------------------

class TestSendMessageStreamToolsPath(BaseStreamingTest):
    """Tests for the tools-enabled streaming path."""

    def _make_tool_execution(
        self,
        name: str = "get_data",
        args: dict = None,
        success: bool = True,
        duration_ms: int = 42,
    ) -> ToolExecution:
        result = ToolResult(success=success, data="tool result", error=None)
        return ToolExecution(
            tool_name=name,
            arguments=args or {"key": "value"},
            result=result,
            duration_ms=duration_ms,
        )

    def _setup_tools_session(self, tool_executions: list, llm_content: str = "final answer"):
        session = self._make_active_session(metadata={"enable_tools": True})
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        assistant_msg = make_message_response("msg-asst", content=llm_content)
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        mock_tool_executor = Mock()
        mock_tool = Mock()
        mock_tool.name = "get_data"
        mock_tool.hint = ""
        mock_tool_executor.tool_registry.get_for_mode.return_value = [mock_tool]
        mock_tool_executor.tool_registry.get_tool_hints_text.return_value = ""

        # Simulate execute_with_tools_stream yielding events then tokens then done
        async def _mock_execute_with_tools_stream(*args, **kwargs):
            for te in tool_executions:
                yield {"type": "tool_start", "data": {"tool_name": te.tool_name, "arguments": te.arguments}}
                yield {"type": "tool_end", "data": {"tool_name": te.tool_name, "success": te.result.success, "duration_ms": te.duration_ms}}
            if llm_content:
                yield {"type": "token", "data": {"content": llm_content}}
            yield {"type": "done", "data": {"tool_executions": tool_executions, "full_content": llm_content}}

        mock_tool_executor.execute_with_tools_stream = Mock(side_effect=_mock_execute_with_tools_stream)

        self.manager = ChatRuntime(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
            tool_executor=mock_tool_executor,
        )

        return session, mock_tool_executor

    @pytest.mark.asyncio
    async def test_tools_path_event_sequence(self):
        """Tools path: status(...) → message_created → tool_start/tool_end → token → done."""
        te = self._make_tool_execution("get_segments")
        _, _ = self._setup_tools_session([te], llm_content="answer")

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        event_types = [e["event"] for e in events]
        assert event_types[:1] == ["status"]
        assert event_types.index("message_created") < event_types.index("tool_start")
        assert "tool_start" in event_types
        assert "tool_end" in event_types
        assert "token" in event_types
        assert event_types[-1] == "done"

    @pytest.mark.asyncio
    async def test_tools_path_tool_start_event_data(self):
        """tool_start event should include tool_name and arguments."""
        te = self._make_tool_execution("get_data", args={"q": "test"})
        self._setup_tools_session([te])

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        tool_start = next(e for e in events if e["event"] == "tool_start")
        assert tool_start["data"]["tool_name"] == "get_data"
        assert tool_start["data"]["arguments"] == {"q": "test"}

    @pytest.mark.asyncio
    async def test_tools_path_tool_end_event_data(self):
        """tool_end event should include tool_name, success, and duration_ms."""
        te = self._make_tool_execution("get_data", success=True, duration_ms=99)
        self._setup_tools_session([te])

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        tool_end = next(e for e in events if e["event"] == "tool_end")
        assert tool_end["data"]["tool_name"] == "get_data"
        assert tool_end["data"]["success"] is True
        assert tool_end["data"]["duration_ms"] == 99

    @pytest.mark.asyncio
    async def test_tools_path_multiple_tools(self):
        """Multiple tool executions should each emit tool_start/tool_end pair."""
        te1 = self._make_tool_execution("tool_one")
        te2 = self._make_tool_execution("tool_two")
        self._setup_tools_session([te1, te2])

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        starts = [e for e in events if e["event"] == "tool_start"]
        ends = [e for e in events if e["event"] == "tool_end"]
        assert len(starts) == 2
        assert len(ends) == 2
        assert {e["data"]["tool_name"] for e in starts} == {"tool_one", "tool_two"}

    @pytest.mark.asyncio
    async def test_tools_path_single_token_event_for_full_content(self):
        """Tools path should emit exactly one token event containing the full LLM response."""
        te = self._make_tool_execution("tool")
        self._setup_tools_session([te], llm_content="the full answer")

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) == 1
        assert token_events[0]["data"]["content"] == "the full answer"

    @pytest.mark.asyncio
    async def test_tools_path_no_token_when_empty_llm_content(self):
        """If LLM returns empty content with tools, no token event should be emitted."""
        te = self._make_tool_execution("tool")
        self._setup_tools_session([te], llm_content="")

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) == 0

    @pytest.mark.asyncio
    async def test_tools_path_tool_executions_stored_in_assistant_metadata(self):
        """Tool execution records should be persisted in the assistant message metadata."""
        te = self._make_tool_execution("my_tool", args={"param": 1}, success=True, duration_ms=55)
        self._setup_tools_session([te], llm_content="result")

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        second_call = self.mock_repo.add_message.call_args_list[1][1]
        metadata = second_call.get("metadata", {}) or {}
        assert "tool_executions" in metadata
        assert len(metadata["tool_executions"]) == 1
        te_record = metadata["tool_executions"][0]
        assert te_record["tool_name"] == "my_tool"
        assert te_record["arguments"] == {"param": 1}
        assert te_record["result"]["success"] is True
        assert te_record["duration_ms"] == 55

    @pytest.mark.asyncio
    async def test_tools_disabled_when_no_executor(self):
        """When tool_executor is None, streaming path should be used even if enable_tools=True."""
        session = self._make_active_session(metadata={"enable_tools": True})
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        assistant_msg = make_message_response("msg-asst")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["streamed"]))

        # manager has no tool_executor (default None)
        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        # Should have used streaming path
        self.mock_llm.stream_with_history.assert_called_once()
        token_events = [e for e in events if e["event"] == "token"]
        assert len(token_events) == 1
        assert token_events[0]["data"]["content"] == "streamed"

    @pytest.mark.asyncio
    async def test_tools_path_done_event_present(self):
        """Tools path should still end with a done event."""
        te = self._make_tool_execution("tool")
        self._setup_tools_session([te], llm_content="done text")

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        assert events[-1]["event"] == "done"

    @pytest.mark.asyncio
    async def test_tools_path_passes_iteration_nudge_for_structured_reply_mode(self):
        """The default generation mode has structured_reply on -- the
        continuation nudge must reach the executor so it can be re-injected
        on every LLM call once a tool round has completed."""
        from src.features.chat.reply_contract import TOOL_LOOP_CONTINUATION_NUDGE

        te = self._make_tool_execution("get_data")
        _, mock_tool_executor = self._setup_tools_session([te])

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        kwargs = mock_tool_executor.execute_with_tools_stream.call_args.kwargs
        assert kwargs["iteration_nudge"] == TOOL_LOOP_CONTINUATION_NUDGE

    @pytest.mark.asyncio
    async def test_tools_path_omits_iteration_nudge_for_non_structured_reply_mode(self):
        """A mode that opts out of the reply contract (structured_reply=False)
        already governs its own output shape -- it must not receive this
        nudge either."""
        from src.features.chat.reply_contract import TOOL_LOOP_CONTINUATION_NUDGE

        te = self._make_tool_execution("get_data")
        session, mock_tool_executor = self._setup_tools_session([te])
        self.manager.chat_mode_registry.require("generation").structured_reply = False

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        kwargs = mock_tool_executor.execute_with_tools_stream.call_args.kwargs
        assert kwargs["iteration_nudge"] is None
        assert kwargs["iteration_nudge"] != TOOL_LOOP_CONTINUATION_NUDGE


# ---------------------------------------------------------------------------
# Error / exception during streaming
# ---------------------------------------------------------------------------

class TestSendMessageStreamErrors(BaseStreamingTest):
    """Tests for error handling during the streaming phase."""

    @pytest.mark.asyncio
    async def test_error_event_emitted_on_llm_exception(self):
        """An exception raised during streaming should emit an error event."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        self.mock_repo.add_message.return_value = user_msg

        async def failing_stream(**_kwargs):
            yield {"type": "token", "content": "partial"}
            raise RuntimeError("LLM connection failed")

        self.mock_llm.stream_with_history = Mock(return_value=failing_stream())

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["data"]["error"] == "stream_error"
        assert "LLM connection failed" in error_events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_error_event_emitted_on_tool_executor_exception(self):
        """An exception in tool_executor should emit an error event."""
        session = self._make_active_session(metadata={"enable_tools": True})
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        self.mock_repo.add_message.return_value = user_msg

        mock_tool_executor = Mock()
        mock_tool = Mock()
        mock_tool.name = "get_data"
        mock_tool.hint = ""
        mock_tool_executor.tool_registry.get_for_mode.return_value = [mock_tool]
        mock_tool_executor.tool_registry.get_tool_hints_text.return_value = ""

        async def _failing_stream(*args, **kwargs):
            raise RuntimeError("tool system crashed")
            yield  # make it an async generator

        mock_tool_executor.execute_with_tools_stream = Mock(side_effect=_failing_stream)

        self.manager = ChatRuntime(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
            tool_executor=mock_tool_executor,
        )

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1
        assert "tool system crashed" in error_events[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_message_created_still_emitted_before_stream_error(self):
        """message_created should still appear even if the stream later errors."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        self.mock_repo.add_message.return_value = user_msg

        async def bad_stream(**_kwargs):
            raise RuntimeError("instant failure")
            yield  # make it a generator

        self.mock_llm.stream_with_history = Mock(return_value=bad_stream())

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        event_types = [e["event"] for e in events]
        assert "message_created" in event_types

    @pytest.mark.asyncio
    async def test_done_not_emitted_after_stream_error(self):
        """done event should NOT be emitted when streaming raises an exception."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        self.mock_repo.add_message.return_value = user_msg

        async def bad_stream(**_kwargs):
            raise RuntimeError("failure")
            yield

        self.mock_llm.stream_with_history = Mock(return_value=bad_stream())

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        done_events = [e for e in events if e["event"] == "done"]
        assert len(done_events) == 0

    @pytest.mark.asyncio
    async def test_error_event_not_raised_as_exception(self):
        """Stream errors should be yielded as events, not propagated as exceptions."""
        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        self.mock_repo.add_message.return_value = user_msg

        async def bad_stream(**_kwargs):
            raise ValueError("unexpected")
            yield

        self.mock_llm.stream_with_history = Mock(return_value=bad_stream())

        # This should NOT raise; errors are yielded as events
        try:
            events = await collect_stream(
                self.manager.send_message_stream(
                    session_id="session-123",
                    user_id="user-123",
                    content="hello",
                )
            )
        except Exception as exc:
            pytest.fail(f"send_message_stream unexpectedly raised: {exc}")

        error_events = [e for e in events if e["event"] == "error"]
        assert len(error_events) == 1


class TestSendMessageStreamResources(BaseStreamingTest):
    """Tests for @resource snapshot resolution in the streaming path."""

    def _setup_with_resources(self):
        from src.platform.resources import ResourceRegistry
        from src.platform.resources.base import BaseResourceProvider, ResolvedResource

        class FakeProvider(BaseResourceProvider):
            @property
            def namespace(self):
                return "fake"

            async def resolve(self, path, ctx):
                if path == ["known"]:
                    return ResolvedResource(
                        uri="fake.known", namespace="fake", kind="thing",
                        title="Known Thing", content="KNOWN CONTENT",
                    )
                return None

            async def suggest(self, path, partial, ctx, limit=15):
                return []

        registry = ResourceRegistry()
        registry.register(FakeProvider())
        self.manager.resource_registry = registry

        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = [
            {"role": "user", "content": "check @fake.known"},
        ]
        user_msg = make_message_response("msg-user", role="user", content="check @fake.known")
        assistant_msg = make_message_response("msg-asst", role="assistant", content="ok")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]
        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["ok"]))

    @pytest.mark.asyncio
    async def test_stream_saves_snapshot_and_injects_block(self):
        self._setup_with_resources()

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="check @fake.known",
                resources=["fake.known"],
            )
        )

        assert [e["event"] for e in events if e["event"] == "done"] == ["done"]
        user_call = self.mock_repo.add_message.call_args_list[0]
        assert user_call.kwargs["metadata"]["resources"][0]["content"] == "KNOWN CONTENT"
        history = self.mock_llm.stream_with_history.call_args.kwargs["messages"]
        assert history[-1]["role"] == "user"
        # -2 is the per-send reply-contract reminder, injected last so it
        # lands closest to the user message; the resource block it displaces
        # sits one slot further back.
        assert history[-2]["role"] == "system"
        assert "Reply format reminder" in history[-2]["content"]
        assert history[-3]["role"] == "system"
        assert "KNOWN CONTENT" in history[-3]["content"]

    @pytest.mark.asyncio
    async def test_stream_unknown_resource_does_not_break(self):
        self._setup_with_resources()

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="check @fake.missing",
                resources=["fake.missing"],
            )
        )

        assert not [e for e in events if e["event"] == "error"]
        assert [e for e in events if e["event"] == "done"]
        history = self.mock_llm.stream_with_history.call_args.kwargs["messages"]
        assert "could not be resolved" in history[-3]["content"]


# ---------------------------------------------------------------------------
# Mode context contributors + per-mode llm_options
# ---------------------------------------------------------------------------

class TestSendMessageStreamModeExtras(BaseStreamingTest):
    """Context-contributor injection and per-mode llm_options plumbing."""

    def _make_custom_mode_manager(self, contributor=None, llm_options=None, tool_executor=None):
        from src.features.chat.modes import ChatMode
        registry = _mode_registry()
        registry.register(ChatMode(
            id="custom",
            name="Custom",
            system_prompt="custom prompt",
            context_contributor=contributor,
            llm_options=llm_options or {},
            source="test-plugin",
        ))
        self.manager = ChatRuntime(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=registry,
            tool_executor=tool_executor,
        )
        return registry

    def _setup_session(self, mode="custom"):
        session = self._make_active_session(mode=mode)
        self.mock_repo.get_session.return_value = session
        history = [{"role": "user", "content": "hello"}]
        self.mock_repo.get_conversation_history.return_value = history
        user_msg = make_message_response("msg-user", role="user", content="hello")
        assistant_msg = make_message_response("msg-asst")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]
        self.mock_llm.stream_with_history = Mock(return_value=make_async_gen(["ok"]))
        return session

    @pytest.mark.asyncio
    async def test_sync_contributor_block_inserted_before_last_user_message(self):
        def contributor(context_metadata, session, user_id):
            return "PLUGIN CONTEXT BLOCK"

        self._make_custom_mode_manager(contributor=contributor)
        self._setup_session()

        await collect_stream(self.manager.send_message_stream(
            session_id="session-123", user_id="user-123", content="hello",
            context_metadata={"foo": "bar"},
        ))

        history = self.mock_llm.stream_with_history.call_args.kwargs["messages"]
        assert history[-1]["role"] == "user"
        # -2 is the per-send reply-contract reminder (structured_reply
        # defaults on); the contributor block it displaces sits one slot
        # further back.
        assert history[-2]["content"] == REPLY_CONTRACT_REMINDER
        assert history[-3] == {"role": "system", "content": "PLUGIN CONTEXT BLOCK"}

    @pytest.mark.asyncio
    async def test_async_contributor_supported(self):
        async def contributor(context_metadata, session, user_id):
            return f"ASYNC {context_metadata.get('foo')} {user_id}"

        self._make_custom_mode_manager(contributor=contributor)
        self._setup_session()

        await collect_stream(self.manager.send_message_stream(
            session_id="session-123", user_id="user-123", content="hello",
            context_metadata={"foo": "bar"},
        ))

        history = self.mock_llm.stream_with_history.call_args.kwargs["messages"]
        assert history[-3] == {"role": "system", "content": "ASYNC bar user-123"}

    @pytest.mark.asyncio
    async def test_contributor_exception_never_breaks_send(self):
        def contributor(context_metadata, session, user_id):
            raise RuntimeError("boom")

        self._make_custom_mode_manager(contributor=contributor)
        self._setup_session()

        events = await collect_stream(self.manager.send_message_stream(
            session_id="session-123", user_id="user-123", content="hello",
        ))

        assert not [e for e in events if e["event"] == "error"]
        assert [e for e in events if e["event"] == "done"]
        history = self.mock_llm.stream_with_history.call_args.kwargs["messages"]
        # The failed contributor left no trace -- the only system message is
        # the per-send reply-contract reminder.
        assert not any("boom" in (m.get("content") or "") for m in history)
        assert [m["content"] for m in history if m["role"] == "system"] == [REPLY_CONTRACT_REMINDER]

    @pytest.mark.asyncio
    async def test_empty_contributor_result_not_inserted(self):
        self._make_custom_mode_manager(contributor=lambda cm, s, u: "   ")
        self._setup_session()

        await collect_stream(self.manager.send_message_stream(
            session_id="session-123", user_id="user-123", content="hello",
        ))

        history = self.mock_llm.stream_with_history.call_args.kwargs["messages"]
        # The blank contributor result was skipped -- the only system message
        # is the per-send reply-contract reminder.
        assert [m["content"] for m in history if m["role"] == "system"] == [REPLY_CONTRACT_REMINDER]

    @pytest.mark.asyncio
    async def test_contributor_block_precedes_resource_block(self):
        from src.platform.resources import ResolvedResource

        self._make_custom_mode_manager(contributor=lambda cm, s, u: "CONTRIB")
        mock_resources = Mock()
        mock_resources.resolve = AsyncMock(return_value=ResolvedResource(
            uri="models.lora.x", namespace="models", kind="model",
            title="x", content="RESOURCE", metadata={},
        ))
        self.manager.resource_registry = mock_resources
        self._setup_session()

        await collect_stream(self.manager.send_message_stream(
            session_id="session-123", user_id="user-123", content="hello",
            resources=["models.lora.x"],
        ))

        history = self.mock_llm.stream_with_history.call_args.kwargs["messages"]
        assert history[-1]["role"] == "user"
        assert history[-2]["content"] == REPLY_CONTRACT_REMINDER
        assert "RESOURCE" in history[-3]["content"]
        assert history[-4]["content"] == "CONTRIB"

    @pytest.mark.asyncio
    async def test_llm_options_reach_stream_with_history(self):
        """Efficiency item 1: mode llm_options flow to the non-tool streaming path."""
        self._make_custom_mode_manager(llm_options={"think": False})
        self._setup_session()

        await collect_stream(self.manager.send_message_stream(
            session_id="session-123", user_id="user-123", content="hello",
        ))

        call_kwargs = self.mock_llm.stream_with_history.call_args.kwargs
        assert call_kwargs["options_override"] == {"think": False}
        assert call_kwargs["mode"] == "custom"

    @pytest.mark.asyncio
    async def test_llm_options_reach_tool_executor(self):
        """Efficiency item 1: mode llm_options flow to the tools streaming path."""
        from src.features.chat.modes import ChatMode

        mock_tool_executor = Mock()
        mock_tool = Mock()
        mock_tool.name = "get_data"
        mock_tool.hint = ""
        mock_tool_executor.tool_registry.get_for_mode.return_value = [mock_tool]
        mock_tool_executor.tool_registry.get_tool_hints_text.return_value = ""

        async def _mock_stream(*args, **kwargs):
            yield {"type": "token", "data": {"content": "ok"}}
            yield {"type": "done", "data": {"tool_executions": [], "full_content": "ok"}}

        mock_tool_executor.execute_with_tools_stream = Mock(side_effect=_mock_stream)

        self._make_custom_mode_manager(
            llm_options={"think": False}, tool_executor=mock_tool_executor,
        )
        # Give the custom mode a tool so the tools path activates
        self._setup_session()

        await collect_stream(self.manager.send_message_stream(
            session_id="session-123", user_id="user-123", content="hello",
        ))

        assert mock_tool_executor.execute_with_tools_stream.called
        call_kwargs = mock_tool_executor.execute_with_tools_stream.call_args.kwargs
        assert call_kwargs["llm_options"] == {"think": False}


# ---------------------------------------------------------------------------
# Behavior timeline: status SSE events + persisted behavior_trace manifest
# ---------------------------------------------------------------------------

def _make_memory_note(note_id: str, key: str = "pref", content: str = "likes cinematic lighting"):
    note = Mock()
    note.id = note_id
    note.key = key
    note.content = content
    return note


class TestSendMessageStreamBehaviorTrace(BaseStreamingTest):
    """Tests for the `status` SSE events and the persisted `behavior_trace` manifest."""

    def _setup(self, resources=None, tool_executions=None, llm_content="answer", pre_chat=False):
        from src.platform.resources import ResourceRegistry
        from src.platform.resources.base import BaseResourceProvider, ResolvedResource

        session = self._make_active_session()
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = make_message_response("msg-user", role="user")
        assistant_msg = make_message_response("msg-asst", content=llm_content)
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        # Memory: one global note. `memory_operations` (as imported into
        # context_builder.py) is patched to a Mock so tests can assert on
        # read_notes calls without exercising the real validation logic -
        # covered separately by tests/features/llm_memory/test_operations.py.
        self._memory_ops_patcher = patch("src.features.chat.context_builder.memory_operations")
        mock_memory_ops = self._memory_ops_patcher.start()
        mock_memory_ops.read_notes.side_effect = lambda repo, user_id, scope, scope_ref=None: (
            [_make_memory_note("note-1")] if scope == "global" else []
        )
        self.manager.llm_memory_repository = Mock()

        # Optional @resource registry.
        if resources:
            class FakeProvider(BaseResourceProvider):
                @property
                def namespace(self):
                    return "fake"

                async def resolve(self, path, ctx):
                    return ResolvedResource(
                        uri="fake." + ".".join(path), namespace="fake", kind="thing",
                        title="Thing", content="CONTENT",
                    )

                async def suggest(self, path, partial, ctx, limit=15):
                    return []

            registry = ResourceRegistry()
            registry.register(FakeProvider())
            self.manager.resource_registry = registry

        # Optional pre-chat action.
        if pre_chat:
            mock_pre_chat = Mock()
            action = Mock()
            action.id = "clear_vram"
            mock_pre_chat.get_enabled_actions.return_value = [action]
            result = Mock()
            result.action_id = "clear_vram"
            mock_pre_chat.execute_actions = AsyncMock(return_value=[result])
            self.manager.pre_chat_action_registry = mock_pre_chat

        if tool_executions is not None:
            mock_tool_executor = Mock()
            mock_tool = Mock()
            mock_tool.name = "get_data"
            mock_tool.hint = ""
            mock_tool_executor.tool_registry.get_for_mode.return_value = [mock_tool]
            mock_tool_executor.tool_registry.get_tool_hints_text.return_value = ""

            async def _mock_execute_with_tools_stream(*args, **kwargs):
                for te in tool_executions:
                    yield {"type": "tool_start", "data": {"tool_name": te.tool_name, "arguments": te.arguments}}
                    yield {"type": "tool_end", "data": {"tool_name": te.tool_name, "success": te.result.success, "duration_ms": te.duration_ms}}
                if llm_content:
                    yield {"type": "token", "data": {"content": llm_content}}
                yield {
                    "type": "done",
                    "data": {
                        "tool_executions": tool_executions,
                        "full_content": llm_content,
                        "tokens_used": 30,
                        "prompt_tokens": 20,
                        "completion_tokens": 10,
                    },
                }

            mock_tool_executor.execute_with_tools_stream = Mock(side_effect=_mock_execute_with_tools_stream)
            self.manager.tool_executor = mock_tool_executor
        else:
            self.mock_llm.stream_with_history = Mock(return_value=make_async_gen([llm_content] if llm_content else []))

        return session, user_msg, assistant_msg

    def teardown_method(self):
        self._memory_ops_patcher.stop()

    @pytest.mark.asyncio
    async def test_status_events_full_sequence_with_resources_memory_tools_prechat(self):
        """A message with resources+memory+tools+pre-chat gets the full status timeline."""
        te = self._make_tool_execution("get_data")
        self._setup(resources=["fake.thing"], tool_executions=[te], pre_chat=True)

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
                resources=["fake.thing"],
            )
        )

        # Extract just the (step, state) pairs for status events, in order.
        status_steps = [
            (e["data"]["step"], e["data"]["state"]) for e in events if e["event"] == "status"
        ]
        assert status_steps == [
            ("resolving_resources", "started"),
            ("resolving_resources", "completed"),
            ("loading_memory", "started"),
            ("loading_memory", "completed"),
            ("running_pre_chat", "started"),
            ("running_pre_chat", "completed"),
            ("thinking", "started"),
            ("answering", "started"),
        ]

        event_types = [e["event"] for e in events]
        # message_created still comes right after the pre-token status block, before tool events.
        assert event_types.index("message_created") < event_types.index("tool_start")

        resolving_completed = next(
            e for e in events if e["event"] == "status" and e["data"]["step"] == "resolving_resources" and e["data"]["state"] == "completed"
        )
        assert resolving_completed["data"]["detail"] == {"count": 1, "uris": ["fake.thing"]}

        memory_completed = next(
            e for e in events if e["event"] == "status" and e["data"]["step"] == "loading_memory" and e["data"]["state"] == "completed"
        )
        detail = memory_completed["data"]["detail"]
        assert detail["note_count"] == 1
        assert detail["by_scope"] == {"global": 1, "preset": 0, "model": 0}
        assert detail["by_scope_dropped"] == {"global": 0, "preset": 0, "model": 0}
        assert detail["injected_chars"] > 0

        pre_chat_completed = next(
            e for e in events if e["event"] == "status" and e["data"]["step"] == "running_pre_chat" and e["data"]["state"] == "completed"
        )
        assert pre_chat_completed["data"]["detail"] == {"actions": ["clear_vram"]}

    @pytest.mark.asyncio
    async def test_status_events_bare_message_no_resources(self):
        """A bare message (no resources, no pre-chat actions) skips those status events."""
        self._setup(resources=None, tool_executions=None, llm_content="hi there")

        events = await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        status_steps = [
            (e["data"]["step"], e["data"]["state"]) for e in events if e["event"] == "status"
        ]
        assert status_steps == [
            ("loading_memory", "started"),
            ("loading_memory", "completed"),
            ("thinking", "started"),
            ("answering", "started"),
        ]

    def _make_tool_execution(self, name, args=None, success=True, duration_ms=42):
        result = ToolResult(success=success, data="tool result", error=None)
        return ToolExecution(
            tool_name=name,
            arguments=args or {"key": "value"},
            result=result,
            duration_ms=duration_ms,
        )

    @pytest.mark.asyncio
    async def test_behavior_trace_persisted_with_tools(self):
        """behavior_trace should be persisted on the assistant message with all keys populated."""
        te = self._make_tool_execution("get_data")
        self._setup(resources=["fake.thing"], tool_executions=[te], pre_chat=True)

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
                resources=["fake.thing"],
            )
        )

        second_call = self.mock_repo.add_message.call_args_list[1][1]
        trace = second_call["metadata"]["behavior_trace"]

        assert trace["version"] == 1
        assert trace["mode"] == "generation"
        assert trace["system_prompt_source"] == "mode:generation"
        assert trace["resources"] == [{"uri": "fake.thing", "type": "thing"}]
        assert trace["memory"]["note_ids"] == ["note-1"]
        assert trace["memory"]["by_scope"] == {"global": 1, "preset": 0, "model": 0}
        assert trace["pre_chat_actions"] == ["clear_vram"]
        assert trace["tools_used"] == ["get_data"]
        assert trace["token_counts"] == {"prompt": 20, "completion": 10}
        assert "context_ledger" in trace

        step_names = [s["step"] for s in trace["steps"]]
        assert step_names == [
            "resolving_resources", "loading_memory", "running_pre_chat", "thinking", "answering",
        ]
        assert all(s["duration_ms"] >= 0 for s in trace["steps"])

    @pytest.mark.asyncio
    async def test_behavior_trace_persisted_without_tools(self):
        """behavior_trace on the plain (no-tools) streaming path."""
        self._setup(resources=None, tool_executions=None, llm_content="just an answer")

        await collect_stream(
            self.manager.send_message_stream(
                session_id="session-123",
                user_id="user-123",
                content="hello",
            )
        )

        second_call = self.mock_repo.add_message.call_args_list[1][1]
        trace = second_call["metadata"]["behavior_trace"]

        assert trace["tools_used"] == []
        assert trace["pre_chat_actions"] == []
        assert trace["resources"] == []
        assert trace["image_attached"] == {"attached": False, "base64_size_kb": None}
        step_names = [s["step"] for s in trace["steps"]]
        assert step_names == ["loading_memory", "thinking", "answering"]

    @pytest.mark.asyncio
    async def test_behavior_trace_records_image_attached_in_stream(self):
        """Streaming path: an attached image should be recorded in the trace
        as attached + its size, never the base64 payload itself."""
        self._setup(resources=None, tool_executions=None, llm_content="just an answer")

        with patch(
            "src.features.chat.conversation.convert_image_to_base64",
            return_value="y" * 1024,  # 1KB of fake base64 payload
        ):
            await collect_stream(
                self.manager.send_message_stream(
                    session_id="session-123",
                    user_id="user-123",
                    content="hello",
                    image_data="/path/to/img.jpg",
                )
            )

        second_call = self.mock_repo.add_message.call_args_list[1][1]
        trace = second_call["metadata"]["behavior_trace"]

        assert trace["image_attached"] == {"attached": True, "base64_size_kb": 1.0}
        assert "y" * 1024 not in str(trace)

