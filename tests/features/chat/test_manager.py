"""Tests for ChatManager."""

import pytest
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from datetime import datetime

from src.features.chat.manager import ChatManager

from src.features.chat.modes import ChatModeRegistry, build_generation_mode


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
    SessionCreationFailedException,
)
from src.features.chat.hooks import CHAT_SESSION_HOOKS


class TestChatManagerInit:
    """Tests for ChatManager initialization."""

    def test_init_with_all_dependencies(self):
        """Should initialize with all required dependencies."""
        mock_repo = Mock()
        mock_llm = Mock()
        mock_processor = Mock()
        mock_plugins = Mock()

        manager = ChatManager(
            chat_repository=mock_repo,
            llm_service=mock_llm,
            response_processor=mock_processor,
            plugin_registry=mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

        assert manager.chat_repository is mock_repo
        assert manager.llm_service is mock_llm
        assert manager.response_processor is mock_processor
        assert manager.plugins is mock_plugins


class TestValidationHelpers:
    """Tests for validation helper methods."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_llm = Mock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    def test_get_session_or_raise_found(self):
        """Should return session if found."""
        mock_session = Mock()
        self.mock_repo.get_session.return_value = mock_session

        result = self.manager._get_session_or_raise("session-123")

        assert result is mock_session
        self.mock_repo.get_session.assert_called_once_with("session-123")

    def test_get_session_or_raise_not_found(self):
        """Should raise SessionNotFoundException if not found."""
        self.mock_repo.get_session.return_value = None

        with pytest.raises(SessionNotFoundException) as exc_info:
            self.manager._get_session_or_raise("session-123")

        assert "session-123" in str(exc_info.value)

    def test_verify_ownership_passes(self):
        """Should not raise if user owns session."""
        mock_session = Mock()
        mock_session.user_id = "user-123"

        # Should not raise
        self.manager._verify_ownership(mock_session, "user-123")

    def test_verify_ownership_fails(self):
        """Should raise AccessDeniedException if user doesn't own session."""
        mock_session = Mock()
        mock_session.user_id = "user-123"

        with pytest.raises(AccessDeniedException):
            self.manager._verify_ownership(mock_session, "different-user")

    def test_verify_active_passes(self):
        """Should not raise if session is active."""
        mock_session = Mock()
        mock_session.status = 'active'

        # Should not raise
        self.manager._verify_active(mock_session)

    def test_verify_active_fails_closed(self):
        """Should raise SessionClosedException if session is not active."""
        mock_session = Mock()
        mock_session.status = 'accepted'

        with pytest.raises(SessionClosedException):
            self.manager._verify_active(mock_session)


class TestCreateSession:
    """Tests for create_session method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_llm = Mock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()

        # Set up default hook execution (no blocking)
        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    def test_create_session_success(self):
        """Should create session successfully."""
        mock_session = Mock()
        mock_session.id = "session-123"
        self.mock_repo.create_session.return_value = mock_session

        result = self.manager.create_session(
            user_id="user-123",
            original_text="Test text",
            llm_config_id="llm-123",
            mode="generation"
        )

        assert result is mock_session
        self.mock_repo.create_session.assert_called_once()

    def test_create_session_executes_before_hook(self):
        """Should execute before_create hook."""
        mock_session = Mock()
        self.mock_repo.create_session.return_value = mock_session

        self.manager.create_session(
            user_id="user-123",
            mode="generation"
        )

        # Verify before_create hook was called
        calls = self.mock_plugins.execute_hook.call_args_list
        assert any(
            CHAT_SESSION_HOOKS.before_create in str(call)
            for call in calls
        )

    def test_create_session_blocked_by_hook(self):
        """Should raise if hook blocks creation."""
        mock_context = Mock()
        mock_context.data = {
            "blocked": True,
            "block_reason": "Custom block reason"
        }
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        with pytest.raises(SessionCreationFailedException) as exc_info:
            self.manager.create_session(
                user_id="user-123",
                mode="generation"
            )

        assert "Custom block reason" in str(exc_info.value)

    def test_create_session_repository_failure(self):
        """Should raise if repository fails to create."""
        self.mock_repo.create_session.return_value = None

        with pytest.raises(SessionCreationFailedException):
            self.manager.create_session(
                user_id="user-123",
                mode="generation"
            )

    def test_create_session_with_system_message(self):
        """Should store system_message in metadata."""
        mock_session = Mock()
        self.mock_repo.create_session.return_value = mock_session

        self.manager.create_session(
            user_id="user-123",
            system_message="Custom system message"
        )

        call_kwargs = self.mock_repo.create_session.call_args[1]
        assert call_kwargs['metadata'] == {'system_message': 'Custom system message'}

class TestGetSession:
    """Tests for get_session method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_plugins = Mock()

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=Mock(),
            response_processor=Mock(),
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    def test_get_session_success(self):
        """Should return session with messages."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        self.mock_repo.get_session_with_messages.return_value = mock_session

        result = self.manager.get_session("session-123", "user-123")

        assert result is mock_session

    def test_get_session_not_found(self):
        """Should raise SessionNotFoundException."""
        self.mock_repo.get_session_with_messages.return_value = None

        with pytest.raises(SessionNotFoundException):
            self.manager.get_session("session-123", "user-123")

    def test_get_session_wrong_user(self):
        """Should raise AccessDeniedException for wrong user."""
        mock_session = Mock()
        mock_session.user_id = "other-user"
        self.mock_repo.get_session_with_messages.return_value = mock_session

        with pytest.raises(AccessDeniedException):
            self.manager.get_session("session-123", "user-123")


class TestDeleteSession:
    """Tests for delete_session method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_plugins = Mock()

        # Default: no blocking
        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=Mock(),
            response_processor=Mock(),
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    def test_delete_session_success(self):
        """Should delete session successfully."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        self.mock_repo.get_session.return_value = mock_session
        self.mock_repo.delete_session.return_value = True

        result = self.manager.delete_session("session-123", "user-123")

        assert result is True
        self.mock_repo.delete_session.assert_called_once_with("session-123")

    def test_delete_session_blocked_by_hook(self):
        """Should raise if hook blocks deletion."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        self.mock_repo.get_session.return_value = mock_session

        mock_context = Mock()
        mock_context.data = {"blocked": True, "block_reason": "Cannot delete"}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        with pytest.raises(AccessDeniedException) as exc_info:
            self.manager.delete_session("session-123", "user-123")

        assert "Cannot delete" in str(exc_info.value)


class TestUpdateSession:
    """Tests for update_session method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_llm = Mock()

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=Mock(),
            plugin_registry=Mock(),
            chat_mode_registry=_mode_registry(),
        )

        self.mock_session = Mock()
        self.mock_session.user_id = "user-123"
        self.mock_session.mode = "generation"
        self.mock_repo.get_session.return_value = self.mock_session

    def test_update_name(self):
        """Should update session name."""
        updated = Mock()
        self.mock_repo.update_session_name.return_value = updated

        result = self.manager.update_session("session-123", "user-123", name="New name")

        assert result is updated
        self.mock_repo.update_session_name.assert_called_once_with("session-123", "New name")

    def test_access_denied_for_other_user(self):
        """Should raise AccessDeniedException when user doesn't own session."""
        with pytest.raises(AccessDeniedException):
            self.manager.update_session("session-123", "other-user", name="New name")


class TestSendMessage:
    """Tests for send_message method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_llm = AsyncMock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()

        # Default: no blocking
        mock_context = Mock()
        mock_context.data = {}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """Should send message and get response."""
        from src.features.chat.dto import MessageResponse

        # Set up session (mock get_session for validation)
        mock_session = Mock()
        mock_session.user_id = "user-123"
        mock_session.status = "active"
        mock_session.llm_config_id = "llm-123"
        mock_session.mode = "generation"
        mock_session.metadata = None
        self.mock_repo.get_session.return_value = mock_session
        self.mock_repo.get_conversation_history.return_value = []

        # Set up messages as actual DTOs
        user_msg = MessageResponse(
            id="msg-1",
            session_id="session-123",
            role="user",
            content="Hello"
        )
        assistant_msg = MessageResponse(
            id="msg-2",
            session_id="session-123",
            role="assistant",
            content="Cleaned response",
            parsed_content={"raw": "Cleaned response"}
        )
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        # Set up LLM response
        mock_llm_response = Mock()
        mock_llm_response.content = "AI response"
        mock_llm_response.model = "test-model"
        mock_llm_response.tokens_used = 100
        mock_llm_response.prompt_tokens = 50
        mock_llm_response.completion_tokens = 50
        self.mock_llm.generate_with_history.return_value = mock_llm_response

        # Set up processor
        self.mock_processor.process.return_value = ("Cleaned response", {"raw": "Cleaned response"})

        result = await self.manager.send_message(
            session_id="session-123",
            user_id="user-123",
            content="Hello"
        )

        assert result.user_message.id == "msg-1"
        assert result.assistant_message.id == "msg-2"

    @pytest.mark.asyncio
    async def test_send_message_session_not_found(self):
        """Should raise SessionNotFoundException."""
        self.mock_repo.get_session.return_value = None

        with pytest.raises(SessionNotFoundException):
            await self.manager.send_message(
                session_id="session-123",
                user_id="user-123",
                content="Hello"
            )

    @pytest.mark.asyncio
    async def test_send_message_wrong_user(self):
        """Should raise AccessDeniedException for wrong user."""
        mock_session = Mock()
        mock_session.user_id = "other-user"
        self.mock_repo.get_session.return_value = mock_session

        with pytest.raises(AccessDeniedException):
            await self.manager.send_message(
                session_id="session-123",
                user_id="user-123",
                content="Hello"
            )

    @pytest.mark.asyncio
    async def test_send_message_session_closed(self):
        """Should raise SessionClosedException for closed session."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        mock_session.status = "accepted"
        self.mock_repo.get_session.return_value = mock_session

        with pytest.raises(SessionClosedException):
            await self.manager.send_message(
                session_id="session-123",
                user_id="user-123",
                content="Hello"
            )

    @pytest.mark.asyncio
    async def test_send_message_no_llm_config(self):
        """Should raise InvalidLLMConfigException if no LLM config."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        mock_session.status = "active"
        mock_session.llm_config_id = None
        self.mock_repo.get_session.return_value = mock_session

        with pytest.raises(InvalidLLMConfigException):
            await self.manager.send_message(
                session_id="session-123",
                user_id="user-123",
                content="Hello"
            )

    @pytest.mark.asyncio
    async def test_send_message_blocked_by_hook(self):
        """Should raise if hook blocks message."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        mock_session.status = "active"
        mock_session.llm_config_id = "llm-123"
        self.mock_repo.get_session.return_value = mock_session

        mock_context = Mock()
        mock_context.data = {"blocked": True, "block_reason": "Spam detected"}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        with pytest.raises(MessageCreationFailedException) as exc_info:
            await self.manager.send_message(
                session_id="session-123",
                user_id="user-123",
                content="Hello"
            )

        assert "Spam detected" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_message_with_image(self):
        """Should convert image data to base64."""
        from src.features.chat.dto import MessageResponse

        mock_session = Mock()
        mock_session.user_id = "user-123"
        mock_session.status = "active"
        mock_session.llm_config_id = "llm-123"
        mock_session.mode = "generation"
        mock_session.metadata = None
        self.mock_repo.get_session.return_value = mock_session
        self.mock_repo.get_conversation_history.return_value = []

        # Use actual DTOs instead of mocks
        user_msg = MessageResponse(
            id="msg-1",
            session_id="session-123",
            role="user",
            content="Hello"
        )
        assistant_msg = MessageResponse(
            id="msg-2",
            session_id="session-123",
            role="assistant",
            content="Response"
        )
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        mock_llm_response = Mock()
        mock_llm_response.content = "AI response"
        mock_llm_response.model = "test"
        mock_llm_response.tokens_used = 10
        mock_llm_response.prompt_tokens = 5
        mock_llm_response.completion_tokens = 5
        self.mock_llm.generate_with_history.return_value = mock_llm_response

        self.mock_processor.process.return_value = ("Response", {"raw": "Response"})

        # Mock image conversion
        with patch('src.features.chat.conversation.convert_image_to_base64') as mock_convert:
            mock_convert.return_value = "base64data"

            await self.manager.send_message(
                session_id="session-123",
                user_id="user-123",
                content="Hello",
                image_data="/path/to/image.jpg"
            )

            mock_convert.assert_called_once_with("/path/to/image.jpg")
            # Verify base64 was passed to LLM
            call_kwargs = self.mock_llm.generate_with_history.call_args[1]
            assert call_kwargs['image_data'] == "base64data"


class TestSendMessageBehaviorTrace:
    """Tests for the persisted `behavior_trace` manifest on the buffered (non-stream) path."""

    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_llm = AsyncMock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()

        mock_context = Mock()
        mock_context.data = {}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])
        self.mock_processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    def _setup_session_and_messages(self):
        from src.features.chat.dto import MessageResponse

        session = Mock()
        session.user_id = "user-123"
        session.status = "active"
        session.llm_config_id = "llm-123"
        session.mode = "generation"
        session.metadata = None
        self.mock_repo.get_session.return_value = session
        self.mock_repo.get_conversation_history.return_value = []

        user_msg = MessageResponse(id="msg-1", session_id="session-123", role="user", content="Hello")
        assistant_msg = MessageResponse(id="msg-2", session_id="session-123", role="assistant", content="Response")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        mock_llm_response = Mock()
        mock_llm_response.content = "AI response"
        mock_llm_response.model = "test-model"
        mock_llm_response.tokens_used = 15
        mock_llm_response.prompt_tokens = 10
        mock_llm_response.completion_tokens = 5
        mock_llm_response.rescues = None
        mock_llm_response.tool_failures = None
        self.mock_llm.generate_with_history.return_value = mock_llm_response

        mock_memory = Mock()
        note = Mock()
        note.id = "note-1"
        note.key = "pref"
        note.content = "likes cinematic lighting"
        mock_memory.read_notes.side_effect = lambda user_id, scope, scope_ref=None: (
            [note] if scope == "global" else []
        )
        self.manager.llm_memory_manager = mock_memory

        return session

    @pytest.mark.asyncio
    async def test_behavior_trace_persisted_in_assistant_metadata(self):
        self._setup_session_and_messages()

        await self.manager.send_message(
            session_id="session-123",
            user_id="user-123",
            content="Hello",
        )

        second_call = self.mock_repo.add_message.call_args_list[1][1]
        trace = second_call["metadata"]["behavior_trace"]

        assert trace["version"] == 1
        assert trace["mode"] == "generation"
        assert trace["system_prompt_source"] == "mode:generation"
        assert trace["resources"] == []
        assert trace["memory"]["note_ids"] == ["note-1"]
        assert trace["memory"]["by_scope"] == {"global": 1, "preset": 0, "model": 0}
        assert trace["memory"]["by_scope_dropped"] == {"global": 0, "preset": 0, "model": 0}
        assert trace["memory"]["injected_chars"] > 0
        assert trace["pre_chat_actions"] == []
        assert trace["tools_used"] == []
        assert trace["token_counts"] == {"prompt": 10, "completion": 5}
        assert trace["image_attached"] == {"attached": False, "base64_size_kb": None}
        assert trace["tool_failures"] is None
        assert "context_ledger" in trace
        step_names = [s["step"] for s in trace["steps"]]
        assert step_names == ["loading_memory", "thinking"]
        assert all(s["duration_ms"] >= 0 for s in trace["steps"])

    @pytest.mark.asyncio
    async def test_behavior_trace_records_image_attached_with_size(self):
        """When an image is attached, the trace should record it was attached
        and its (resolved, base64) size — never the base64 payload itself, so
        the admin trace viewer can answer "was an image attached" at a glance
        without anyone reading raw request JSON."""
        self._setup_session_and_messages()

        with patch(
            "src.features.chat.conversation.convert_image_to_base64",
            return_value="x" * 2048,  # 2KB of fake base64 payload
        ):
            await self.manager.send_message(
                session_id="session-123",
                user_id="user-123",
                content="Hello",
                image_data="/path/to/image.jpg",
            )

        second_call = self.mock_repo.add_message.call_args_list[1][1]
        trace = second_call["metadata"]["behavior_trace"]

        assert trace["image_attached"] == {"attached": True, "base64_size_kb": 2.0}
        # The base64 payload itself must never be persisted in the trace.
        assert "x" * 2048 not in str(trace)


class TestMemoryCountedInHistoryBudget:
    """The recalled-memory block must count against the history token budget
    (instead of escaping it unbounded) and must survive trimming even when
    it alone would blow a tight budget, because older turns get dropped
    first."""

    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_llm = AsyncMock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()

        mock_context = Mock()
        mock_context.data = {}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])
        self.mock_processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        self.mock_settings = Mock()
        self.mock_settings.get_setting = Mock(return_value=10)  # 10 tokens = 40 chars

        self.mock_memory = Mock()
        note = Mock()
        note.id = "note-1"
        note.key = "pref"
        note.content = "a distinctive recalled fact about this user"
        self.mock_memory.read_notes.side_effect = lambda user_id, scope, scope_ref=None: (
            [note] if scope == "global" else []
        )

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
            llm_memory_manager=self.mock_memory,
            settings_manager=self.mock_settings,
        )

        session = Mock()
        session.user_id = "user-123"
        session.status = "active"
        session.llm_config_id = "llm-123"
        session.mode = "generation"
        session.metadata = None
        self.mock_repo.get_session.return_value = session

        # Six old turns, each well over the 40-char budget on its own, plus
        # the current user message.
        old_turns = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"old turn {i} " + "x" * 60}
            for i in range(6)
        ]
        self.mock_repo.get_conversation_history.return_value = old_turns + [
            {"role": "user", "content": "current question"},
        ]

        from src.features.chat.dto import MessageResponse
        user_msg = MessageResponse(id="msg-1", session_id="session-123", role="user", content="current question")
        assistant_msg = MessageResponse(id="msg-2", session_id="session-123", role="assistant", content="Response")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        mock_llm_response = Mock()
        mock_llm_response.content = "AI response"
        mock_llm_response.model = "test-model"
        mock_llm_response.tokens_used = 15
        mock_llm_response.prompt_tokens = 10
        mock_llm_response.completion_tokens = 5
        mock_llm_response.rescues = None
        mock_llm_response.tool_failures = None
        self.mock_llm.generate_with_history.return_value = mock_llm_response

    @pytest.mark.asyncio
    async def test_old_turns_drop_while_memory_block_survives(self):
        await self.manager.send_message(
            session_id="session-123", user_id="user-123", content="current question",
        )

        sent = self.mock_llm.generate_with_history.call_args.kwargs["messages"]

        # All six old turns were dropped -- the memory block alone already
        # exceeds the 40-char budget, so nothing before it fits.
        assert not any("old turn" in (m.get("content") or "") for m in sent)
        assert sent[-1]["content"] == "current question"
        assert any("a distinctive recalled fact" in (m.get("content") or "") for m in sent)
        # memory block + current question + the per-send reply-contract reminder
        assert len(sent) == 3

        trace = self.mock_repo.add_message.call_args_list[1][1]["metadata"]["behavior_trace"]
        assert trace["history"]["truncated"] is True
        assert trace["history"]["messages_total"] == 8  # 6 old turns + memory block + current
        assert trace["memory"]["injected_chars"] > 0

    @pytest.mark.asyncio
    async def test_context_ledger_present_with_plausible_values(self):
        await self.manager.send_message(
            session_id="session-123", user_id="user-123", content="current question",
        )

        trace = self.mock_repo.add_message.call_args_list[1][1]["metadata"]["behavior_trace"]
        ledger = trace["context_ledger"]

        for key in ("system_prompt", "tool_schemas", "memory", "history"):
            assert ledger[key]["chars"] >= 0
            assert ledger[key]["est_tokens"] == ledger[key]["chars"] // 4
        # The messages actually sent (memory block + current question + the
        # per-send reply-contract reminder) account for the history entry's size.
        assert ledger["history"]["chars"] > 0
        assert ledger["history"]["message_count"] == 3
        assert ledger["memory"]["chars"] > 0
        assert isinstance(ledger["total_est_tokens"], int)


class TestApproveToolExecutionPreservesBehaviorTrace:
    """The tool-approval metadata rewrite must not drop the persisted behavior_trace."""

    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_llm = Mock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    @pytest.mark.asyncio
    async def test_behavior_trace_survives_approval_rewrite(self):
        session = Mock()
        session.id = "session-123"
        session.user_id = "user-123"
        session.mode = "generation"
        self.mock_repo.get_session.return_value = session

        behavior_trace = {
            "version": 1, "mode": "generation", "system_prompt_source": "mode:generation",
            "resources": [], "memory": {"note_ids": [], "by_scope": {"global": 0, "preset": 0, "model": 0}},
            "pre_chat_actions": [], "tools_used": ["get_data"], "token_counts": {"prompt": 10, "completion": 5},
            "steps": [{"step": "thinking", "duration_ms": 5}],
        }
        message = Mock()
        message.session_id = "session-123"
        message.metadata = {
            "behavior_trace": behavior_trace,
            "tool_executions": [{"tool_name": "get_data", "arguments": {}, "pending_approval": True, "result": {}}],
        }
        self.mock_repo.get_message.return_value = message

        await self.manager.approve_tool_execution(
            session_id="session-123",
            user_id="user-123",
            message_id="msg-1",
            tool_index=0,
            approved=False,
        )

        persisted = self.mock_repo.update_message_metadata.call_args.args[1]
        assert persisted["behavior_trace"] == behavior_trace


class TestAcceptSession:
    """Tests for accept_session method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_plugins = Mock()

        # Default: no blocking
        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=Mock(),
            response_processor=Mock(),
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    def test_accept_session_success(self):
        """Should accept session successfully."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        self.mock_repo.get_session.return_value = mock_session
        self.mock_repo.get_messages.return_value = []
        self.mock_repo.accept_session.return_value = True

        result = self.manager.accept_session("session-123", "user-123")

        assert result is True
        self.mock_repo.accept_session.assert_called_once_with("session-123")

    def test_accept_session_blocked(self):
        """Should raise if hook blocks acceptance."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        self.mock_repo.get_session.return_value = mock_session
        self.mock_repo.get_messages.return_value = []

        mock_context = Mock()
        mock_context.data = {"blocked": True, "block_reason": "Validation failed"}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        with pytest.raises(AccessDeniedException) as exc_info:
            self.manager.accept_session("session-123", "user-123")

        assert "Validation failed" in str(exc_info.value)


class TestRejectSession:
    """Tests for reject_session method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_plugins = Mock()

        # Default: no blocking
        mock_context = Mock()
        mock_context.data = {"blocked": False}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=Mock(),
            response_processor=Mock(),
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    def test_reject_session_success(self):
        """Should reject session successfully."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        self.mock_repo.get_session.return_value = mock_session
        self.mock_repo.reject_session.return_value = True

        result = self.manager.reject_session("session-123", "user-123")

        assert result is True
        self.mock_repo.reject_session.assert_called_once_with("session-123")

    def test_reject_session_blocked(self):
        """Should raise if hook blocks rejection."""
        mock_session = Mock()
        mock_session.user_id = "user-123"
        self.mock_repo.get_session.return_value = mock_session

        mock_context = Mock()
        mock_context.data = {"blocked": True, "block_reason": "Cannot reject"}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        with pytest.raises(AccessDeniedException) as exc_info:
            self.manager.reject_session("session-123", "user-123")

        assert "Cannot reject" in str(exc_info.value)


class TestHookExecution:
    """Tests for hook execution behavior."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_repo = Mock()
        self.mock_plugins = Mock()

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=Mock(),
            response_processor=Mock(),
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

    def test_execute_hook_returns_data_and_blocked(self):
        """Should return context data and blocked status."""
        mock_context = Mock()
        mock_context.data = {"key": "value", "blocked": True}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        data, blocked, ctx = self.manager._execute_hook(
            CHAT_SESSION_HOOKS.before_create,
            {"input": "data"}
        )

        assert data == {"key": "value", "blocked": True}
        assert blocked is True

    def test_execute_hook_defaults_blocked_to_false(self):
        """Should default blocked to False if not in context."""
        mock_context = Mock()
        mock_context.data = {"key": "value"}  # No 'blocked' key
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        data, blocked, ctx = self.manager._execute_hook(
            CHAT_SESSION_HOOKS.before_create,
            {}
        )

        assert blocked is False


class TestSendMessageResources:
    """Tests for @resource snapshot resolution in send_message."""

    def setup_method(self):
        from src.platform.resources import ResourceRegistry
        from src.platform.resources.base import (
            BaseResourceProvider, ResolvedResource, ResourceSuggestion,
        )

        class FakeProvider(BaseResourceProvider):
            @property
            def namespace(self):
                return "fake"

            async def resolve(self, path, ctx):
                if path == ["known"]:
                    return ResolvedResource(
                        uri="fake.known", namespace="fake", kind="thing",
                        title="Known Thing", content="KNOWN CONTENT",
                        metadata={"id": "k1"},
                    )
                return None

            async def suggest(self, path, partial, ctx, limit=15):
                return [ResourceSuggestion(uri="fake.known", label="Known Thing")]

        self.mock_repo = Mock()
        self.mock_llm = AsyncMock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()
        mock_context = Mock()
        mock_context.data = {}
        self.mock_plugins.execute_hook.return_value = (mock_context, [])

        registry = ResourceRegistry()
        registry.register(FakeProvider())
        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
            resource_registry=registry,
        )

        from src.features.chat.dto import MessageResponse
        mock_session = Mock()
        mock_session.user_id = "user-123"
        mock_session.status = "active"
        mock_session.llm_config_id = "llm-123"
        mock_session.mode = "generation"
        mock_session.metadata = None
        self.mock_repo.get_session.return_value = mock_session
        self.mock_repo.get_conversation_history.return_value = [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "earlier reply"},
            {"role": "user", "content": "check @fake.known"},
        ]
        user_msg = MessageResponse(id="m1", session_id="s1", role="user", content="check @fake.known")
        assistant_msg = MessageResponse(id="m2", session_id="s1", role="assistant", content="ok")
        self.mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        llm_response = Mock()
        llm_response.content = "ok"
        llm_response.model = "m"
        llm_response.tokens_used = 1
        llm_response.prompt_tokens = 1
        llm_response.completion_tokens = 1
        self.mock_llm.generate_with_history.return_value = llm_response
        self.mock_processor.process.return_value = ("ok", {"raw": "ok"})

    @pytest.mark.asyncio
    async def test_resources_snapshot_saved_on_user_message(self):
        await self.manager.send_message(
            session_id="s1", user_id="user-123",
            content="check @fake.known", resources=["fake.known"],
        )
        user_call = self.mock_repo.add_message.call_args_list[0]
        metadata = user_call.kwargs["metadata"]
        assert metadata["resources"] == [{
            "uri": "fake.known", "kind": "thing", "title": "Known Thing",
            "metadata": {"id": "k1"}, "content": "KNOWN CONTENT",
        }]

    @pytest.mark.asyncio
    async def test_resource_block_inserted_before_last_user_message(self):
        await self.manager.send_message(
            session_id="s1", user_id="user-123",
            content="check @fake.known", resources=["fake.known"],
        )
        history = self.mock_llm.generate_with_history.call_args.kwargs["messages"]
        assert history[-1] == {"role": "user", "content": "check @fake.known"}
        # -2 is the per-send reply-contract reminder (structured_reply is on
        # by default) -- it's injected last so it lands closest to the user
        # message; the resource block it displaces sits one slot further back.
        assert history[-2]["role"] == "system"
        assert "Reply format reminder" in history[-2]["content"]
        assert history[-3]["role"] == "system"
        assert "snapshot at send time" in history[-3]["content"]
        assert "KNOWN CONTENT" in history[-3]["content"]

    @pytest.mark.asyncio
    async def test_unknown_resource_becomes_error_note_and_send_succeeds(self):
        result = await self.manager.send_message(
            session_id="s1", user_id="user-123",
            content="check @fake.missing", resources=["fake.missing"],
        )
        assert result.assistant_message.id == "m2"
        user_call = self.mock_repo.add_message.call_args_list[0]
        metadata = user_call.kwargs["metadata"]
        assert metadata["resources"][0]["kind"] == "error"
        history = self.mock_llm.generate_with_history.call_args.kwargs["messages"]
        assert "could not be resolved" in history[-3]["content"]

    @pytest.mark.asyncio
    async def test_no_resources_no_block(self):
        await self.manager.send_message(
            session_id="s1", user_id="user-123", content="plain",
        )
        user_call = self.mock_repo.add_message.call_args_list[0]
        assert user_call.kwargs["metadata"] is None
        history = self.mock_llm.generate_with_history.call_args.kwargs["messages"]
        # The only system message is the per-send reply-contract reminder --
        # no resource snapshot was attached, so no resource block exists.
        assert not any("snapshot at send time" in (m.get("content") or "") for m in history)

    @pytest.mark.asyncio
    async def test_suggest_resources_delegates_to_registry(self):
        suggestions = await self.manager.suggest_resources(
            query="fake.", mode_id="generation", user_id="user-123",
        )
        assert suggestions[0].uri == "fake.known"


class TestInjectMemoryBlock:
    """Tests for ChatManager._inject_memory_block."""

    def _make_note(self, key="pref", content="likes cinematic lighting"):
        note = Mock()
        note.key = key
        note.content = content
        return note

    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_llm = Mock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()
        self.mock_memory = Mock()
        self.mock_model_index = Mock()

        self.manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
            llm_memory_manager=self.mock_memory,
            model_index_manager=self.mock_model_index,
        )

    def test_inserts_block_when_notes_exist(self):
        self.mock_memory.read_notes.return_value = [self._make_note()]
        history = [{"role": "user", "content": "hello"}]

        self.manager._inject_memory_block(history, context_metadata=None, user_id="user-1")

        assert len(history) == 2
        assert history[0]["role"] == "system"
        assert "pref" in history[0]["content"]
        assert "likes cinematic lighting" in history[0]["content"]
        assert history[1]["content"] == "hello"

    def test_inserts_nothing_when_empty(self):
        self.mock_memory.read_notes.return_value = []
        history = [{"role": "user", "content": "hello"}]

        self.manager._inject_memory_block(history, context_metadata=None, user_id="user-1")

        assert history == [{"role": "user", "content": "hello"}]

    def test_groups_global_preset_and_model_notes(self):
        global_notes = [self._make_note(key="g", content="global fact")]
        preset_notes = [self._make_note(key="p", content="preset fact")]
        model_notes = [self._make_note(key="m", content="model fact")]
        self.mock_memory.read_notes.side_effect = [global_notes, preset_notes, model_notes]

        model = Mock()
        model.id = "model-1"
        self.mock_model_index.model_repo.get_by_file_path.return_value = model

        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "preset-1",
                "form_data": {"checkpoint": "models/checkpoints/sdxl.safetensors"},
            }
        }

        self.manager._inject_memory_block(history, context_metadata, user_id="user-1")

        block = history[0]["content"]
        assert "[global]" in block
        assert "[this preset]" in block
        assert "[this model]" in block
        assert "global fact" in block
        assert "preset fact" in block
        assert "model fact" in block

    def test_no_manager_does_nothing(self):
        manager = ChatManager(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )
        history = [{"role": "user", "content": "hello"}]

        manager._inject_memory_block(history, context_metadata=None, user_id="user-1")

        assert history == [{"role": "user", "content": "hello"}]

    def test_header_nudges_write_memory_when_available(self):
        self.mock_memory.read_notes.return_value = [self._make_note()]
        history = [{"role": "user", "content": "hello"}]

        self.manager._context.inject_memory_block(
            history, context_metadata=None, user_id="user-1", write_memory_available=True,
        )

        assert "call write_memory to add more" in history[0]["content"]

    def test_header_omits_write_memory_nudge_when_disabled(self):
        """The injected block must not point at a tool the session can't call."""
        self.mock_memory.read_notes.return_value = [self._make_note()]
        history = [{"role": "user", "content": "hello"}]

        self.manager._context.inject_memory_block(
            history, context_metadata=None, user_id="user-1", write_memory_available=False,
        )

        assert "write_memory" not in history[0]["content"]
        assert "persistent memory — use it" in history[0]["content"]

    def test_read_failure_never_raises(self):
        self.mock_memory.read_notes.side_effect = RuntimeError("db down")
        history = [{"role": "user", "content": "hello"}]

        self.manager._inject_memory_block(history, context_metadata=None, user_id="user-1")

        assert history == [{"role": "user", "content": "hello"}]

    def test_group_overflow_appends_visible_line_and_reports_dropped_count(self):
        notes = [self._make_note(key=f"k{i}", content=f"note {i}") for i in range(25)]
        self.mock_memory.read_notes.return_value = notes
        history = [{"role": "user", "content": "hello"}]

        result = self.manager._context.inject_memory_block(history, context_metadata=None, user_id="user-1")

        block = history[0]["content"]
        assert "(+5 older notes not shown — consolidate or prune in the memory panel)" in block
        assert "k19" in block  # last of the 20 shown
        assert "k20" not in block  # first of the 5 dropped
        assert result["by_scope_dropped"] == {"global": 5, "preset": 0, "model": 0}

    def test_group_under_cap_reports_no_drops(self):
        self.mock_memory.read_notes.return_value = [self._make_note()]
        history = [{"role": "user", "content": "hello"}]

        result = self.manager._context.inject_memory_block(history, context_metadata=None, user_id="user-1")

        assert result["by_scope_dropped"] == {"global": 0, "preset": 0, "model": 0}
        assert "not shown" not in history[0]["content"]

    def test_long_note_content_clipped_with_visible_ellipsis(self):
        long_content = "y" * 600
        self.mock_memory.read_notes.return_value = [self._make_note(content=long_content)]
        history = [{"role": "user", "content": "hello"}]

        self.manager._context.inject_memory_block(history, context_metadata=None, user_id="user-1")

        block = history[0]["content"]
        assert ("y" * 500 + "…") in block
        assert ("y" * 501) not in block

    def test_return_value_reports_injected_chars(self):
        self.mock_memory.read_notes.return_value = [self._make_note()]
        history = [{"role": "user", "content": "hello"}]

        result = self.manager._context.inject_memory_block(history, context_metadata=None, user_id="user-1")

        assert result["injected_chars"] == len(history[0]["content"])
        assert result["injected_chars"] > 0

    def test_no_notes_reports_zero_injected_chars(self):
        self.mock_memory.read_notes.return_value = []
        history = [{"role": "user", "content": "hello"}]

        result = self.manager._context.inject_memory_block(history, context_metadata=None, user_id="user-1")

        assert result["injected_chars"] == 0
        assert result["by_scope_dropped"] == {"global": 0, "preset": 0, "model": 0}


def _mock_model(model_type, filename, triggers=None, guidance=None, description="", model_id="id"):
    """A model whose to_dict() feeds model_to_dict the fields the block reads."""
    model = Mock()
    data = {
        "id": model_id,
        "filename": filename,
        "model_type": model_type,
        "description": description,
        "tags": [],
    }
    if triggers:
        data["triggers"] = triggers
    if guidance:
        data["prompting_guidance"] = guidance
    model.to_dict.return_value = data
    return model


class TestInjectWorkspaceBlock:
    """Tests for ChatContextBuilder.inject_workspace_block."""

    def setup_method(self):
        self.mock_model_index = Mock()
        # preset_manager=None → build_model_field_metadata returns {}, so the
        # checkpoint/LoRA split relies on each model's own DB model_type.
        self.manager = ChatManager(
            chat_repository=Mock(),
            llm_service=Mock(),
            response_processor=Mock(),
            plugin_registry=Mock(),
            chat_mode_registry=_mode_registry(),
            model_index_manager=self.mock_model_index,
            preset_manager=None,
        )

    def _wire_models(self, by_path):
        self.mock_model_index.model_repo.get_by_file_path.side_effect = (
            lambda path, **kw: by_path.get(path)
        )

    def test_lists_checkpoint_and_active_loras_excluding_zero_strength(self):
        self._wire_models({
            "sdxl.safetensors": _mock_model("checkpoint", "sdxl.safetensors"),
            "lora_a.safetensors": _mock_model(
                "lora", "lora_a.safetensors", triggers=["magic", "glow"],
            ),
            "lora_b.safetensors": _mock_model("lora", "lora_b.safetensors"),
        })
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "native/SDXL",
                "mode": "image",
                "form_data": {
                    "checkpoint": "sdxl.safetensors",
                    "loras": [
                        {"model": "lora_a.safetensors", "strength": 0.8},
                        {"model": "lora_b.safetensors", "strength": 0},
                    ],
                },
            }
        }

        summary = self.manager._context.inject_workspace_block(history, context_metadata)

        assert len(history) == 2
        block = history[0]["content"]
        assert history[0]["role"] == "system"
        assert "Preset: native/SDXL · Mode: image" in block
        assert "Checkpoint: sdxl.safetensors" in block
        assert "lora_a.safetensors" in block
        assert "(strength 0.8)" in block
        assert "triggers: magic, glow" in block
        # Zero-strength LoRA is a disabled selection — never listed.
        assert "lora_b.safetensors" not in block

        assert summary == {
            "preset": "native/SDXL",
            "checkpoint": "sdxl.safetensors",
            "loras": ["lora_a.safetensors"],
            "guidance_included": False,
        }

    def test_guidance_is_capped(self):
        long_guidance = "G" * 400
        self._wire_models({
            "sdxl.safetensors": _mock_model(
                "checkpoint", "sdxl.safetensors", guidance=long_guidance,
            ),
        })
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "native/SDXL",
                "form_data": {"checkpoint": "sdxl.safetensors"},
            }
        }

        summary = self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "call get_model_info for the full guidance" in block
        assert long_guidance not in block
        assert "G" * 240 in block
        assert summary["guidance_included"] is True

    def test_absent_form_state_injects_nothing(self):
        history = [{"role": "user", "content": "hello"}]

        summary = self.manager._context.inject_workspace_block(history, context_metadata=None)

        assert history == [{"role": "user", "content": "hello"}]
        assert summary == {
            "preset": None, "checkpoint": None, "loras": [], "guidance_included": False,
        }

    def test_empty_form_state_injects_nothing(self):
        history = [{"role": "user", "content": "hello"}]

        summary = self.manager._context.inject_workspace_block(
            history, {"form_state": {}},
        )

        assert history == [{"role": "user", "content": "hello"}]
        assert summary["checkpoint"] is None and summary["loras"] == []

    def test_lookup_failure_never_raises(self):
        self.mock_model_index.model_repo.get_by_file_path.side_effect = RuntimeError("db down")
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "native/SDXL",
                "form_data": {"checkpoint": "sdxl.safetensors"},
            }
        }

        summary = self.manager._context.inject_workspace_block(history, context_metadata)

        # A preset with no resolvable models still yields the preset line, and the
        # per-lookup failure is swallowed inside lookup_model — never raised here.
        assert summary["preset"] == "native/SDXL"
        assert summary["checkpoint"] is None

    def test_variant_appears_in_preset_line_when_present(self):
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "native/LTX-2",
                "mode": "video",
                "variant": "img2vid",
                "form_data": {},
            }
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Preset: native/LTX-2 · Mode: video · Variant: img2vid" in block

    def test_variant_omitted_from_preset_line_when_absent(self):
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/LTX-2", "mode": "video", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Preset: native/LTX-2 · Mode: video" in block
        assert "Variant:" not in block

    def test_steering_line_points_at_update_video_director_when_active(self):
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "native/LTX-2",
                "mode": "video",
                "form_data": {},
                "video_director": {"active": True, "doc": {}, "capabilities": {}},
            }
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Video Director: active" in block
        assert "update_video_director" in block
        assert "update_form_settings" not in block

    def test_steering_line_points_at_update_form_settings_when_inactive(self):
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "native/LTX-2",
                "mode": "video",
                "form_data": {},
                "video_director": {"active": False, "doc": {}, "capabilities": {}},
            }
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "update_form_settings" in block
        assert "update_video_director" not in block

    def test_steering_line_points_at_update_form_settings_when_video_director_absent(self):
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/LTX-2", "mode": "video", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "update_form_settings" in block
        assert "update_video_director" not in block


class TestInjectWorkspaceBlockMusicDirector:
    """Tests for the Music Director summary `inject_workspace_block` adds
    (compact mode/description/sections/settings), mirroring the Video
    Director coverage above."""

    def setup_method(self):
        self.manager = ChatManager(
            chat_repository=Mock(),
            llm_service=Mock(),
            response_processor=Mock(),
            plugin_registry=Mock(),
            chat_mode_registry=_mode_registry(),
            model_index_manager=Mock(),
            preset_manager=None,
        )

    def test_steering_line_and_summary_when_active(self):
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "native/Music3",
                "mode": "song",
                "form_data": {},
                "music_director": {
                    "active": True,
                    "doc": {
                        "mode": "director",
                        "description": "warm 90s boom-bap, vinyl crackle",
                        "sections": [
                            {"kind": "verse", "lyrics": "riding through the city lights\nsecond line"},
                            {"kind": "chorus", "lyrics": "we rise, we shine"},
                        ],
                        "settings": {"duration": 90, "bpm": 92, "key": None, "time_signature": None},
                    },
                    "capabilities": {},
                },
            }
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Music Director: active" in block
        assert "update_music_director" in block
        assert "Mode: director" in block
        assert "warm 90s boom-bap, vinyl crackle" in block
        assert "Sections (2):" in block
        assert "verse: riding through the city lights" in block
        assert "second line" not in block  # only the first lyric line is shown
        assert "chorus: we rise, we shine" in block
        assert "duration=90" in block
        assert "bpm=92" in block

    def test_nothing_added_when_inactive(self):
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "native/Music3",
                "mode": "song",
                "form_data": {},
                "music_director": {"active": False, "doc": None, "capabilities": None},
            }
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Music Director" not in block

    def test_nothing_added_when_music_director_absent(self):
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/Music3", "mode": "song", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Music Director" not in block


def _make_preset_template(name="Some Preset", llm=None):
    """Just enough of a PresetTemplate for inject_workspace_block's lookup."""
    return SimpleNamespace(name=name, llm=llm)


class TestInjectWorkspaceBlockLLMContext:
    """Tests for the `llm:` block's effect on inject_workspace_block — preset
    name in the header, `llm.guide` injection, the `llm.context.form` summary
    listing, `guidance_chars` override, and `context.fields` guidance gating.
    See docs/presets.md "LLM context"."""

    def setup_method(self):
        self.mock_model_index = Mock()
        self.mock_preset_manager = Mock()
        self.manager = ChatManager(
            chat_repository=Mock(),
            llm_service=Mock(),
            response_processor=Mock(),
            plugin_registry=Mock(),
            chat_mode_registry=_mode_registry(),
            model_index_manager=self.mock_model_index,
            preset_manager=self.mock_preset_manager,
        )

    def _wire_models(self, by_path):
        self.mock_model_index.model_repo.get_by_file_path.side_effect = (
            lambda path, **kw: by_path.get(path)
        )

    def _wire_preset(self, preset_template):
        self.mock_preset_manager.file_repo.find_preset_by_id.return_value = preset_template

    def _wire_form_schema(self, properties):
        self.mock_preset_manager.get_form_schema.return_value = {"form_schema": {"properties": properties}}

    def test_header_uses_preset_name_not_raw_id(self):
        self._wire_preset(_make_preset_template(name="Qwen Image"))
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "01K0W24A3RADXXABH16YQ7KF00", "mode": "txt2img", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Preset: Qwen Image · Mode: txt2img" in block
        assert "01K0W24A3RADXXABH16YQ7KF00" not in block

    def test_header_falls_back_to_raw_id_when_preset_not_found(self):
        """No plugin-manager resolution possible - degrade to exactly the old
        behavior instead of showing nothing."""
        self.mock_preset_manager.file_repo.find_preset_by_id.return_value = None
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/SDXL", "mode": "image", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Preset: native/SDXL · Mode: image" in block

    def test_no_llm_block_omits_guide_and_form_section(self):
        """A preset with no `llm:` block gets exactly the prior behavior."""
        self._wire_preset(_make_preset_template(llm=None))
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/SDXL", "mode": "image", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Prompting guide:" not in block
        assert "Form fields" not in block

    def test_guide_is_injected_when_present(self):
        self._wire_preset(_make_preset_template(llm={
            "guide": "Prefer comma-separated tags over full sentences.",
            "context": {"form": "off"},
        }))
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/SDXL", "mode": "image", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Prompting guide:" in block
        assert "Prefer comma-separated tags over full sentences." in block
        # form: off - the guide still shows, but no form-field listing.
        assert "Form fields" not in block

    def test_guide_is_capped(self):
        long_guide = "G" * 4000
        self._wire_preset(_make_preset_template(llm={"guide": long_guide, "context": {"form": "off"}}))
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/SDXL", "mode": "image", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "G" * 3000 in block
        assert long_guide not in block

    def test_form_summary_lists_fields_with_ai_hint(self):
        self._wire_preset(_make_preset_template(llm={"context": {"form": "summary"}}))
        self._wire_form_schema({
            "checkpoint": {
                "title": "Checkpoint", "type": "string",
                "ai_hint": "The base model driving overall style.",
                "configuration": {"model_type": "checkpoint"},
            },
            "steps": {"title": "Steps", "type": "integer", "minimum": 1, "maximum": 50, "default": 28},
        })
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/SDXL", "mode": "txt2img", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Form fields (mode: txt2img):" in block
        assert "- checkpoint" in block
        assert "The base model driving overall style." in block
        assert "- steps" in block
        # summary mode: no range/default details.
        assert "range 1-50" not in block

    def test_form_full_includes_range_and_default(self):
        self._wire_preset(_make_preset_template(llm={"context": {"form": "full"}}))
        self._wire_form_schema({
            "steps": {"title": "Steps", "type": "integer", "minimum": 1, "maximum": 50, "default": 28},
        })
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/SDXL", "mode": "txt2img", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "range 1-50" in block
        assert "default=28" in block

    def test_form_off_suppresses_listing(self):
        self._wire_preset(_make_preset_template(llm={"context": {"form": "off"}}))
        self._wire_form_schema({
            "steps": {"title": "Steps", "type": "integer", "ai_hint": "should never appear"},
        })
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/SDXL", "mode": "txt2img", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Form fields" not in block
        assert "should never appear" not in block

    def test_guidance_chars_override_widens_the_cap(self):
        long_guidance = "G" * 400
        self._wire_preset(_make_preset_template(llm={"context": {"form": "off", "guidance_chars": 350}}))
        self._wire_models({
            "sdxl.safetensors": _mock_model("checkpoint", "sdxl.safetensors", guidance=long_guidance),
        })
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/SDXL", "form_data": {"checkpoint": "sdxl.safetensors"}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "G" * 350 in block
        # the default 240-char cap must NOT have kicked in.
        assert "G" * 351 not in block

    def test_context_fields_restricts_guidance_to_allowed_fields(self):
        self._wire_preset(_make_preset_template(llm={"context": {"form": "off", "fields": ["checkpoint"]}}))
        self._wire_models({
            "sdxl.safetensors": _mock_model("checkpoint", "sdxl.safetensors", guidance="checkpoint guidance text"),
            "lora_a.safetensors": _mock_model(
                "lora", "lora_a.safetensors", triggers=["magic"], guidance="lora guidance text",
            ),
        })
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {
                "preset": "native/SDXL",
                "form_data": {
                    "checkpoint": "sdxl.safetensors",
                    "loras": [{"model": "lora_a.safetensors", "strength": 0.8}],
                },
            }
        }

        summary = self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        # checkpoint is in `fields:` - its guidance is included.
        assert "checkpoint guidance text" in block
        # loras is NOT in `fields:` - the LoRA is still listed by name/trigger, but
        # its guidance text is withheld.
        assert "lora_a.safetensors" in block
        assert "triggers: magic" in block
        assert "lora guidance text" not in block
        assert summary["loras"] == ["lora_a.safetensors"]

    def test_mode_override_replaces_base_guide(self):
        self._wire_preset(_make_preset_template(llm={
            "guide": "Base guide: plain description.",
            "context": {"form": "off"},
            "modes": {
                "refs": {"guide": "Refs guide: six-section brief."},
            },
        }))
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/H3", "mode": "refs", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Refs guide: six-section brief." in block
        assert "Base guide: plain description." not in block

    def test_mode_without_override_falls_back_to_base_guide(self):
        self._wire_preset(_make_preset_template(llm={
            "guide": "Base guide: plain description.",
            "context": {"form": "off"},
            "modes": {
                "refs": {"guide": "Refs guide: six-section brief."},
            },
        }))
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/H3", "mode": "video", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "Base guide: plain description." in block
        assert "Refs guide: six-section brief." not in block

    def test_mode_override_is_capped(self):
        long_guide = "R" * 4000
        self._wire_preset(_make_preset_template(llm={
            "guide": "short base",
            "context": {"form": "off"},
            "modes": {"refs": {"guide": long_guide}},
        }))
        history = [{"role": "user", "content": "hello"}]
        context_metadata = {
            "form_state": {"preset": "native/H3", "mode": "refs", "form_data": {}}
        }

        self.manager._context.inject_workspace_block(history, context_metadata)

        block = history[0]["content"]
        assert "R" * 3000 in block
        assert long_guide not in block


class TestPromiseSentenceRenders:
    """The workspace-note promise sentence survives tool-conditional rendering."""

    def test_promise_sentence_present_and_renders(self):
        registry = _mode_registry()
        mode = registry.require("generation")

        rendered = registry.resolve_system_prompt(mode, "hints", allowed_names=["get_form_state"])

        assert "your current workspace" in rendered
        assert "prompting guidance an admin wrote for them" in rendered
        assert "{{" not in rendered


class TestToolCallFormatGuidance:
    """The tools prompt names the correct call format and warns off near-miss markup."""

    def test_builtin_prompt_warns_against_tool_action_markup(self):
        registry = _mode_registry()
        mode = registry.require("generation")

        rendered = registry.resolve_system_prompt(mode, "hints", allowed_names=["get_form_state"])

        assert "never as <tool_action> markup" in rendered
        assert "{{" not in rendered

    def test_native_client_injects_concrete_tool_call_example(self):
        from src.features.llm.clients.native import NativeLLMClient

        injected = NativeLLMClient._inject_tools_into_system_message(
            "sys",
            [{"function": {"name": "get_form_state", "description": "d", "parameters": {}}}],
        )

        assert '<tool_call>{"name": "get_form_state", "arguments": {}}</tool_call>' in injected
        assert "<tool_action>" in injected  # names the format NOT to use


class TestBehaviorTraceRescues:
    """The behavior-trace manifest records malformed-call rescues per turn."""

    def _trace(self, rescues):
        from src.features.chat.conversation import ConversationRunner

        mode = Mock()
        mode.id = "generation"
        session = Mock()
        session.metadata = None
        return ConversationRunner._build_behavior_trace(
            mode=mode,
            session=session,
            resolved_resources=[],
            memory_result={},
            workspace_result=None,
            pre_chat_results=[],
            tool_executions=[],
            rescues=rescues,
            prompt_tokens=1,
            completion_tokens=1,
            steps=[],
        )

    def test_rescue_records_land_in_trace(self):
        records = [
            {"tool_name": "update_video_director", "repaired": True, "original_format": "tool_action_tag"}
        ]
        assert self._trace(records)["rescues"] == records

    def test_no_rescue_is_none_in_trace(self):
        assert self._trace(None)["rescues"] is None


class TestBehaviorTraceToolFailures:
    """The behavior-trace manifest passes through the executor's tool_failures
    (when present) rather than dropping it, guarding for its absence on
    response objects that don't carry the attribute yet."""

    def _trace(self, tool_failures=None):
        from src.features.chat.conversation import ConversationRunner

        mode = Mock()
        mode.id = "generation"
        session = Mock()
        session.metadata = None
        return ConversationRunner._build_behavior_trace(
            mode=mode,
            session=session,
            resolved_resources=[],
            memory_result={},
            workspace_result=None,
            pre_chat_results=[],
            tool_executions=[],
            rescues=None,
            prompt_tokens=1,
            completion_tokens=1,
            steps=[],
            tool_failures=tool_failures,
        )

    def test_tool_failures_land_in_trace_when_present(self):
        failures = [{"tool_name": "run_generation", "error": "timeout"}]
        assert self._trace(failures)["tool_failures"] == failures

    def test_tool_failures_default_to_none(self):
        assert self._trace()["tool_failures"] is None

    def test_omitted_kwarg_still_defaults_to_none(self):
        """A caller that hasn't been updated to pass tool_failures (e.g. an
        existing test double) must not break -- the parameter has to default
        cleanly."""
        from src.features.chat.conversation import ConversationRunner

        mode = Mock()
        mode.id = "generation"
        session = Mock()
        session.metadata = None
        trace = ConversationRunner._build_behavior_trace(
            mode=mode, session=session, resolved_resources=[], memory_result={},
            workspace_result=None, pre_chat_results=[], tool_executions=[], rescues=None,
            prompt_tokens=1, completion_tokens=1, steps=[],
        )
        assert trace["tool_failures"] is None

    @pytest.mark.asyncio
    async def test_buffered_send_message_pulls_tool_failures_from_llm_response(self):
        """Mirrors how execute_with_tools's return value carries tool_failures
        through to the trace -- getattr on the final LLM response object, same
        pattern already used for rescues."""
        mock_repo = Mock()
        mock_llm = AsyncMock()
        mock_processor = Mock()
        mock_plugins = Mock()
        mock_context = Mock()
        mock_context.data = {}
        mock_plugins.execute_hook.return_value = (mock_context, [])
        mock_processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        manager = ChatManager(
            chat_repository=mock_repo,
            llm_service=mock_llm,
            response_processor=mock_processor,
            plugin_registry=mock_plugins,
            chat_mode_registry=_mode_registry(),
        )

        session = Mock()
        session.user_id = "user-123"
        session.status = "active"
        session.llm_config_id = "llm-123"
        session.mode = "generation"
        session.metadata = None
        mock_repo.get_session.return_value = session
        mock_repo.get_conversation_history.return_value = []

        from src.features.chat.dto import MessageResponse
        user_msg = MessageResponse(id="msg-1", session_id="s", role="user", content="Hello")
        assistant_msg = MessageResponse(id="msg-2", session_id="s", role="assistant", content="Response")
        mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        mock_llm_response = Mock()
        mock_llm_response.content = "AI response"
        mock_llm_response.model = "test-model"
        mock_llm_response.tokens_used = 15
        mock_llm_response.prompt_tokens = 10
        mock_llm_response.completion_tokens = 5
        mock_llm_response.rescues = None
        mock_llm_response.tool_failures = [{"tool_name": "run_generation", "error": "boom"}]
        mock_llm.generate_with_history.return_value = mock_llm_response

        await manager.send_message(session_id="session-123", user_id="user-123", content="Hello")

        second_call = mock_repo.add_message.call_args_list[1][1]
        trace = second_call["metadata"]["behavior_trace"]
        assert trace["tool_failures"] == [{"tool_name": "run_generation", "error": "boom"}]


class TestSendMessageToolsPathIterationNudge:
    """ConversationRunner must pass the tool-loop continuation nudge through
    to the executor for structured-reply modes, and withhold it for modes
    that opt out (their own prompt already governs the reply shape)."""

    def _setup_tools_session(self, structured_reply: bool):
        mock_repo = Mock()
        mock_llm = AsyncMock()
        mock_processor = Mock()
        mock_plugins = Mock()
        mock_context = Mock()
        mock_context.data = {}
        mock_plugins.execute_hook.return_value = (mock_context, [])
        mock_processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        registry = _mode_registry()
        registry.require("generation").structured_reply = structured_reply

        mock_tool_executor = Mock()
        mock_tool = Mock()
        mock_tool.name = "get_data"
        mock_tool.hint = ""
        mock_tool_executor.tool_registry.get_for_mode.return_value = [mock_tool]
        mock_tool_executor.tool_registry.get_tool_hints_text.return_value = ""

        response = Mock()
        response.content = "done"
        response.rescues = None
        response.tool_failures = None
        response.tokens_used = 1
        response.prompt_tokens = 1
        response.completion_tokens = 1
        mock_tool_executor.execute_with_tools = AsyncMock(return_value=(response, []))

        manager = ChatManager(
            chat_repository=mock_repo,
            llm_service=mock_llm,
            response_processor=mock_processor,
            plugin_registry=mock_plugins,
            chat_mode_registry=registry,
            tool_executor=mock_tool_executor,
        )

        session = Mock()
        session.user_id = "user-123"
        session.status = "active"
        session.llm_config_id = "llm-123"
        session.mode = "generation"
        session.metadata = {"enable_tools": True}
        mock_repo.get_session.return_value = session
        mock_repo.get_conversation_history.return_value = []

        from src.features.chat.dto import MessageResponse
        user_msg = MessageResponse(id="msg-1", session_id="s", role="user", content="Hello")
        assistant_msg = MessageResponse(id="msg-2", session_id="s", role="assistant", content="Response")
        mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        return manager, mock_tool_executor

    @pytest.mark.asyncio
    async def test_structured_reply_mode_passes_nudge(self):
        from src.features.chat.reply_contract import TOOL_LOOP_CONTINUATION_NUDGE

        manager, mock_tool_executor = self._setup_tools_session(structured_reply=True)
        await manager.send_message(session_id="session-123", user_id="user-123", content="hi")

        kwargs = mock_tool_executor.execute_with_tools.call_args.kwargs
        assert kwargs["iteration_nudge"] == TOOL_LOOP_CONTINUATION_NUDGE

    @pytest.mark.asyncio
    async def test_non_structured_reply_mode_omits_nudge(self):
        manager, mock_tool_executor = self._setup_tools_session(structured_reply=False)
        await manager.send_message(session_id="session-123", user_id="user-123", content="hi")

        kwargs = mock_tool_executor.execute_with_tools.call_args.kwargs
        assert kwargs["iteration_nudge"] is None
