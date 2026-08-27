"""Tests for ChatController (refactored version).

These tests verify that the thin controller properly delegates to ChatRuntime
and correctly maps exceptions to HTTP responses.
"""

import json
import pytest
from unittest.mock import Mock, AsyncMock
from datetime import datetime

from fastapi.responses import StreamingResponse

from src.features.chat.routes import ChatController
from src.features.chat.turns import ChatTurnRegistry
from src.features.chat.dto import (
    CreateSessionRequest, SendMessageRequest, UpdateSessionRequest,
    SessionResponse, MessageResponse, SendMessageResponse
)
from src.platform.security.user import User
from src.features.chat.exceptions import (
    SessionNotFoundException,
    AccessDeniedException,
    SessionClosedException,
    InvalidLLMConfigException,
    MessageCreationFailedException,
    SessionCreationFailedException,
)


class TestChatController:
    """Tests for ChatController"""

    @pytest.fixture
    def mock_chat_manager(self):
        """Mock ChatRuntime"""
        return Mock()

    @pytest.fixture
    def controller(self, mock_chat_manager):
        """Create controller with mocked ChatRuntime"""
        return ChatController(chat_runtime=mock_chat_manager, turn_registry=ChatTurnRegistry())

    @pytest.fixture
    def sample_user(self):
        """Sample user"""
        return User(
            id="user-123",
            username="testuser",
            email="test@example.com",
            password_hash="hash",
            account_type="USER"
        )

    @pytest.fixture
    def sample_session_response(self):
        """Sample SessionResponse DTO"""
        return SessionResponse(
            id="session-123",
            user_id="user-123",
            mode="generation",
            name="Test Session",
            status="active",
            llm_config_id="llm-config-1",
            original_text="Original prompt",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            closed_at=None,
            message_count=0,
            messages=None
        )

    @pytest.fixture
    def sample_message_response(self):
        """Sample MessageResponse DTO"""
        return MessageResponse(
            id="msg-123",
            session_id="session-123",
            role="user",
            content="Hello AI!",
            parsed_content=None,
            created_at=datetime.now().isoformat(),
            tokens_used=None,
            prompt_tokens=None,
            completion_tokens=None
        )

    @pytest.fixture
    def sample_create_request(self):
        """Sample session creation request"""
        return CreateSessionRequest(
            original_text="Test prompt",
            llm_config_id="llm-config-1",
            mode="generation",
            name="New Session"
        )

    @pytest.fixture
    def sample_send_request(self):
        """Sample send message request"""
        return SendMessageRequest(
            content="Hello, AI!",
            image_data=None
        )

    # Session creation tests
    def test_create_session_success(
        self, controller, mock_chat_manager, sample_create_request, sample_session_response, sample_user
    ):
        """Test successful session creation"""
        mock_chat_manager.create_session.return_value = sample_session_response

        result = controller.create_session(sample_create_request, sample_user)

        assert result.success is True
        assert result.data["id"] == "session-123"
        assert result.data["name"] == "Test Session"
        mock_chat_manager.create_session.assert_called_once_with(
            user_id="user-123",
            original_text="Test prompt",
            llm_config_id="llm-config-1",
            mode="generation",
            name="New Session",
            system_message=None,
            enabled_tools=None,
        )

    def test_create_session_with_system_message(
        self, controller, mock_chat_manager, sample_session_response, sample_user
    ):
        """Test session creation with system message"""
        mock_chat_manager.create_session.return_value = sample_session_response

        request = CreateSessionRequest(
            original_text="Test",
            llm_config_id="llm-1",
            mode="generation",
            system_message="Custom system message"
        )
        result = controller.create_session(request, sample_user)

        assert result.success is True
        call_kwargs = mock_chat_manager.create_session.call_args[1]
        assert call_kwargs['system_message'] == "Custom system message"

    def test_create_session_failure(
        self, controller, mock_chat_manager, sample_create_request, sample_user
    ):
        """Test session creation failure from manager"""
        mock_chat_manager.create_session.side_effect = SessionCreationFailedException("Failed")

        result = controller.create_session(sample_create_request, sample_user)

        assert result.success is False
        assert "session_creation_failed" in result.error

    def test_create_session_exception(
        self, controller, mock_chat_manager, sample_create_request, sample_user
    ):
        """Test session creation with unexpected exception"""
        mock_chat_manager.create_session.side_effect = Exception("Database error")

        result = controller.create_session(sample_create_request, sample_user)

        assert result.success is False
        assert "Database error" in result.message

    # Get sessions tests
    def test_get_sessions_success(
        self, controller, mock_chat_manager, sample_session_response, sample_user
    ):
        """Test getting recent sessions"""
        mock_chat_manager.list_sessions.return_value = ([sample_session_response], 1)

        result = controller.get_sessions(sample_user, mode="generation", limit=20)

        assert result.success is True
        assert len(result.data["sessions"]) == 1
        assert result.data["sessions"][0]["id"] == "session-123"
        assert result.data["total"] == 1

    def test_get_sessions_empty(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test getting sessions when none exist"""
        mock_chat_manager.list_sessions.return_value = ([], 0)

        result = controller.get_sessions(sample_user)

        assert result.success is True
        assert len(result.data["sessions"]) == 0

    # Get single session tests
    def test_get_session_success(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test getting a single session with messages"""
        session_with_messages = SessionResponse(
            id="session-123",
            user_id="user-123",
            mode="generation",
            name="Test Session",
            status="active",
            message_count=1,
            messages=[
                MessageResponse(
                    id="msg-1",
                    session_id="session-123",
                    role="user",
                    content="Hello",
                    created_at=datetime.now().isoformat()
                )
            ]
        )
        mock_chat_manager.get_session.return_value = session_with_messages

        result = controller.get_session("session-123", sample_user)

        assert result.success is True
        assert result.data["id"] == "session-123"
        assert len(result.data["messages"]) == 1

    def test_get_session_not_found(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test getting a nonexistent session"""
        mock_chat_manager.get_session.side_effect = SessionNotFoundException("Not found")

        result = controller.get_session("nonexistent", sample_user)

        assert result.success is False
        assert "session_not_found" in result.error

    def test_get_session_access_denied(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test getting session with access denied"""
        mock_chat_manager.get_session.side_effect = AccessDeniedException("No access")

        result = controller.get_session("session-123", sample_user)

        assert result.success is False
        assert "access_denied" in result.error

    # Send message tests
    @pytest.mark.asyncio
    async def test_send_message_success(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test sending a message successfully"""
        user_msg = MessageResponse(
            id="user-msg",
            session_id="session-123",
            role="user",
            content="Hello, AI!",
            created_at=datetime.now().isoformat()
        )
        assistant_msg = MessageResponse(
            id="assistant-msg",
            session_id="session-123",
            role="assistant",
            content="Hi there!",
            parsed_content={"raw": "Hi there!"},
            created_at=datetime.now().isoformat(),
            tokens_used=100,
            prompt_tokens=20,
            completion_tokens=80
        )

        mock_chat_manager.send_message = AsyncMock(return_value=SendMessageResponse(
            user_message=user_msg,
            assistant_message=assistant_msg
        ))

        result = await controller.send_message("session-123", sample_send_request, sample_user)

        assert result.success is True
        assert result.data["user_message"]["role"] == "user"
        assert result.data["assistant_message"]["role"] == "assistant"
        assert result.data["assistant_message"]["content"] == "Hi there!"

    @pytest.mark.asyncio
    async def test_send_message_session_not_found(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test sending message to nonexistent session"""
        mock_chat_manager.send_message = AsyncMock(
            side_effect=SessionNotFoundException("Not found")
        )

        result = await controller.send_message("nonexistent", sample_send_request, sample_user)

        assert result.success is False
        assert "session_not_found" in result.error

    @pytest.mark.asyncio
    async def test_send_message_access_denied(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test sending message with access denied"""
        mock_chat_manager.send_message = AsyncMock(
            side_effect=AccessDeniedException("No access")
        )

        result = await controller.send_message("session-123", sample_send_request, sample_user)

        assert result.success is False
        assert "access_denied" in result.error

    @pytest.mark.asyncio
    async def test_send_message_session_closed(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test sending message to closed session"""
        mock_chat_manager.send_message = AsyncMock(
            side_effect=SessionClosedException("Session closed")
        )

        result = await controller.send_message("session-123", sample_send_request, sample_user)

        assert result.success is False
        assert "session_closed" in result.error

    @pytest.mark.asyncio
    async def test_send_message_no_llm_config(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test sending message when session has no LLM config"""
        mock_chat_manager.send_message = AsyncMock(
            side_effect=InvalidLLMConfigException("No config")
        )

        result = await controller.send_message("session-123", sample_send_request, sample_user)

        assert result.success is False
        assert "no_llm_config" in result.error

    @pytest.mark.asyncio
    async def test_send_message_llm_error(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test sending message with LLM error"""
        mock_chat_manager.send_message = AsyncMock(
            side_effect=ValueError("LLM error")
        )

        result = await controller.send_message("session-123", sample_send_request, sample_user)

        assert result.success is False
        assert "llm_error" in result.error

    # Accept session tests
    def test_accept_session_success(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test accepting a session"""
        mock_chat_manager.accept_session.return_value = True

        result = controller.accept_session("session-123", sample_user)

        assert result.success is True
        mock_chat_manager.accept_session.assert_called_once_with("session-123", "user-123")

    def test_accept_session_not_found(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test accepting nonexistent session"""
        mock_chat_manager.accept_session.side_effect = SessionNotFoundException("Not found")

        result = controller.accept_session("nonexistent", sample_user)

        assert result.success is False
        assert "session_not_found" in result.error

    def test_accept_session_access_denied(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test accepting session with access denied"""
        mock_chat_manager.accept_session.side_effect = AccessDeniedException("Blocked by plugin")

        result = controller.accept_session("session-123", sample_user)

        assert result.success is False
        assert "access_denied" in result.error

    # Reject session tests
    def test_reject_session_success(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test rejecting a session"""
        mock_chat_manager.reject_session.return_value = True

        result = controller.reject_session("session-123", sample_user)

        assert result.success is True
        mock_chat_manager.reject_session.assert_called_once_with("session-123", "user-123")

    def test_reject_session_not_found(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test rejecting nonexistent session"""
        mock_chat_manager.reject_session.side_effect = SessionNotFoundException("Not found")

        result = controller.reject_session("nonexistent", sample_user)

        assert result.success is False
        assert "session_not_found" in result.error

    def test_reject_session_access_denied(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test rejecting session with access denied"""
        mock_chat_manager.reject_session.side_effect = AccessDeniedException("Blocked")

        result = controller.reject_session("session-123", sample_user)

        assert result.success is False
        assert "access_denied" in result.error

    # Update session tests
    def test_update_session_success(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test updating session name"""
        updated_session = SessionResponse(
            id="session-123",
            user_id="user-123",
            mode="generation",
            name="Updated Name",
            status="active",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        mock_chat_manager.update_session.return_value = updated_session

        request = UpdateSessionRequest(name="Updated Name")
        result = controller.update_session("session-123", request, sample_user)

        assert result.success is True
        assert result.data["name"] == "Updated Name"

    def test_update_session_not_found(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test updating nonexistent session"""
        mock_chat_manager.update_session.side_effect = SessionNotFoundException("Not found")

        request = UpdateSessionRequest(name="New Name")
        result = controller.update_session("nonexistent", request, sample_user)

        assert result.success is False
        assert "session_not_found" in result.error

    def test_update_session_access_denied(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test updating session with access denied"""
        mock_chat_manager.update_session.side_effect = AccessDeniedException("No access")

        request = UpdateSessionRequest(name="New Name")
        result = controller.update_session("session-123", request, sample_user)

        assert result.success is False
        assert "access_denied" in result.error

    def test_update_session_passes_llm_config_id(
        self, controller, mock_chat_manager, sample_user
    ):
        """Switching the composer's LLM config on an existing session must
        reach the manager, not just update local frontend state."""
        updated_session = SessionResponse(
            id="session-123",
            user_id="user-123",
            mode="generation",
            status="active",
            llm_config_id="llm-b",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        mock_chat_manager.update_session.return_value = updated_session

        request = UpdateSessionRequest(llm_config_id="llm-b")
        result = controller.update_session("session-123", request, sample_user)

        assert result.success is True
        mock_chat_manager.update_session.assert_called_once_with(
            session_id="session-123",
            user_id=sample_user.id,
            name=None,
            llm_config_id="llm-b",
        )

    # Delete session tests
    def test_delete_session_success(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test deleting a session"""
        mock_chat_manager.delete_session.return_value = True

        result = controller.delete_session("session-123", sample_user)

        assert result.success is True
        mock_chat_manager.delete_session.assert_called_once_with("session-123", "user-123")

    def test_delete_session_not_found(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test deleting nonexistent session"""
        mock_chat_manager.delete_session.side_effect = SessionNotFoundException("Not found")

        result = controller.delete_session("nonexistent", sample_user)

        assert result.success is False
        assert "session_not_found" in result.error

    def test_delete_session_access_denied(
        self, controller, mock_chat_manager, sample_user
    ):
        """Test deleting session with access denied (blocked by hook)"""
        mock_chat_manager.delete_session.side_effect = AccessDeniedException("Blocked")

        result = controller.delete_session("session-123", sample_user)

        assert result.success is False
        assert "access_denied" in result.error

    # Streaming endpoint tests

    @pytest.mark.asyncio
    async def test_send_message_stream_returns_streaming_response(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that send_message_stream returns a StreamingResponse"""
        async def mock_stream():
            yield {"event": "message_created", "data": {"user_message_id": "user-1", "assistant_message_id": ""}}
            yield {"event": "token", "data": {"content": "Hello"}}
            yield {"event": "done", "data": {"assistant_message": {}, "user_message": {}}}

        mock_chat_manager.send_message_stream = Mock(return_value=mock_stream())

        result = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"

    @pytest.mark.asyncio
    async def test_send_message_stream_headers(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that StreamingResponse has correct SSE headers"""
        async def mock_stream():
            yield {"event": "token", "data": {"content": "Hi"}}

        mock_chat_manager.send_message_stream = Mock(return_value=mock_stream())

        result = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        assert result.headers.get("Cache-Control") == "no-cache"
        assert result.headers.get("X-Accel-Buffering") == "no"

    @pytest.mark.asyncio
    async def test_send_message_stream_sse_format(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that streamed events follow the SSE format: event: type\\ndata: json\\n\\n"""
        async def mock_stream():
            yield {"event": "message_created", "data": {"user_message_id": "user-1", "assistant_message_id": ""}}
            yield {"event": "token", "data": {"content": "Hello"}}
            yield {"event": "token", "data": {"content": " world"}}
            yield {
                "event": "done",
                "data": {
                    "assistant_message": {"id": "asst-1", "role": "assistant", "content": "Hello world"},
                    "user_message": {"id": "user-1", "role": "user", "content": "Hi"}
                }
            }

        mock_chat_manager.send_message_stream = Mock(return_value=mock_stream())

        response = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_output = "".join(chunks)
        events = [e for e in full_output.split("\n\n") if e.strip()]

        assert len(events) == 4

        # Verify first event: message_created
        lines = events[0].split("\n")
        assert lines[0] == "event: message_created"
        assert lines[1].startswith("data: ")
        data = json.loads(lines[1][len("data: "):])
        assert data["user_message_id"] == "user-1"

        # Verify token events
        lines = events[1].split("\n")
        assert lines[0] == "event: token"
        data = json.loads(lines[1][len("data: "):])
        assert data["content"] == "Hello"

        lines = events[2].split("\n")
        assert lines[0] == "event: token"
        data = json.loads(lines[1][len("data: "):])
        assert data["content"] == " world"

        # Verify done event
        lines = events[3].split("\n")
        assert lines[0] == "event: done"
        data = json.loads(lines[1][len("data: "):])
        assert "assistant_message" in data
        assert "user_message" in data

    @pytest.mark.asyncio
    async def test_send_message_stream_calls_manager_with_correct_args(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that the manager is called with the correct arguments"""
        async def mock_stream():
            yield {"event": "done", "data": {}}

        mock_chat_manager.send_message_stream = Mock(return_value=mock_stream())

        response = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        # Consume the iterator to trigger the generator
        async for _ in response.body_iterator:
            pass

        mock_chat_manager.send_message_stream.assert_called_once_with(
            session_id="session-123",
            user_id="user-123",
            content="Hello, AI!",
            image_data=None,
            context_metadata=None,
            resources=None,
        )

    @pytest.mark.asyncio
    async def test_send_message_stream_session_not_found_error_event(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that SessionNotFoundException yields an SSE error event"""
        async def mock_error_stream():
            raise SessionNotFoundException("Not found")
            yield  # Make it a generator

        mock_chat_manager.send_message_stream = Mock(return_value=mock_error_stream())

        response = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_output = "".join(chunks)
        assert "event: error" in full_output
        assert "session_not_found" in full_output

        data_line = [l for l in full_output.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[len("data: "):])
        assert data["error"] == "session_not_found"

    @pytest.mark.asyncio
    async def test_send_message_stream_access_denied_error_event(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that AccessDeniedException yields an SSE error event"""
        async def mock_error_stream():
            raise AccessDeniedException("No access")
            yield  # Make it a generator

        mock_chat_manager.send_message_stream = Mock(return_value=mock_error_stream())

        response = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_output = "".join(chunks)
        assert "event: error" in full_output

        data_line = [l for l in full_output.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[len("data: "):])
        assert data["error"] == "access_denied"

    @pytest.mark.asyncio
    async def test_send_message_stream_session_closed_error_event(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that SessionClosedException yields an SSE error event"""
        async def mock_error_stream():
            raise SessionClosedException("Session is closed")
            yield  # Make it a generator

        mock_chat_manager.send_message_stream = Mock(return_value=mock_error_stream())

        response = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_output = "".join(chunks)
        assert "event: error" in full_output

        data_line = [l for l in full_output.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[len("data: "):])
        assert data["error"] == "session_closed"

    @pytest.mark.asyncio
    async def test_send_message_stream_invalid_llm_config_error_event(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that InvalidLLMConfigException yields an SSE error event"""
        async def mock_error_stream():
            raise InvalidLLMConfigException("No config")
            yield  # Make it a generator

        mock_chat_manager.send_message_stream = Mock(return_value=mock_error_stream())

        response = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_output = "".join(chunks)
        assert "event: error" in full_output

        data_line = [l for l in full_output.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[len("data: "):])
        assert data["error"] == "no_llm_config"

    @pytest.mark.asyncio
    async def test_send_message_stream_value_error_event(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that ValueError yields a generic stream_error SSE event"""
        async def mock_error_stream():
            raise ValueError("Invalid LLM parameter")
            yield  # Make it a generator

        mock_chat_manager.send_message_stream = Mock(return_value=mock_error_stream())

        response = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_output = "".join(chunks)
        assert "event: error" in full_output

        data_line = [l for l in full_output.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[len("data: "):])
        assert data["error"] == "stream_error"
        assert "Invalid LLM parameter" in data["message"]

    @pytest.mark.asyncio
    async def test_send_message_stream_generic_exception_error_event(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that an unexpected exception yields a generic stream_error SSE event"""
        async def mock_error_stream():
            raise RuntimeError("Unexpected failure")
            yield  # Make it a generator

        mock_chat_manager.send_message_stream = Mock(return_value=mock_error_stream())

        response = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_output = "".join(chunks)
        assert "event: error" in full_output

        data_line = [l for l in full_output.split("\n") if l.startswith("data: ")][0]
        data = json.loads(data_line[len("data: "):])
        assert data["error"] == "stream_error"
        assert "Unexpected failure" in data["message"]

    @pytest.mark.asyncio
    async def test_send_message_stream_empty_stream(
        self, controller, mock_chat_manager, sample_send_request, sample_user
    ):
        """Test that an empty stream produces no SSE events"""
        async def mock_empty_stream():
            return
            yield  # Make it a generator

        mock_chat_manager.send_message_stream = Mock(return_value=mock_empty_stream())

        response = await controller.send_message_stream("session-123", sample_send_request, sample_user)

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        assert "".join(chunks) == ""


class TestChatModesEndpoints:
    """Tests for GET /api/chat/modes and mode-filtered GET /api/chat/tools."""

    @pytest.fixture
    def real_manager_controller(self):
        """Controller whose manager carries a real mode registry + tool registry."""
        from src.features.chat.modes import ChatModeRegistry, build_generation_mode
        from src.features.llm.tools.registry import ToolRegistry
        from src.features.llm.tools.builtin import register_builtin_tools

        mode_registry = ChatModeRegistry()
        mode_registry.register(build_generation_mode())

        tool_registry = ToolRegistry()
        register_builtin_tools(tool_registry)

        manager = Mock()
        manager.chat_mode_registry = mode_registry
        manager.tool_executor = Mock()
        manager.tool_executor.tool_registry = tool_registry
        return ChatController(chat_runtime=manager, turn_registry=ChatTurnRegistry())

    def test_get_modes(self, real_manager_controller):
        result = real_manager_controller.get_modes()

        assert result.success is True
        modes = result.data["modes"]
        assert len(modes) == 1
        gen = modes[0]
        assert gen["id"] == "generation"
        assert gen["source"] == "builtin"
        assert gen["default_route_prefixes"]
        assert "get_form_state" in gen["tools"]
        assert "write_memory" in gen["tools"]  # global tools included

    def test_list_tools_unfiltered_includes_metadata(self, real_manager_controller):
        result = real_manager_controller.list_tools()

        assert result.success is True
        tools = {t["name"]: t for t in result.data["tools"]}
        assert tools["get_form_state"]["mode"] == "generation"
        assert tools["get_form_state"]["icon"] == "clipboard-list"
        assert tools["get_form_state"]["label"] == "Get Form State"
        assert tools["get_form_state"]["group"] == "Form & segments"
        assert tools["get_form_state"]["user_description"]
        assert tools["write_memory"]["mode"] is None  # global
        assert tools["write_memory"]["group"] == "Memory"

    def test_list_tools_filtered_by_mode(self, real_manager_controller):
        result = real_manager_controller.list_tools(mode="generation")

        assert result.success is True
        names = [t["name"] for t in result.data["tools"]]
        assert "get_form_state" in names
        assert "write_memory" in names  # global visible in every mode

    def test_list_tools_unknown_mode(self, real_manager_controller):
        result = real_manager_controller.list_tools(mode="nope")

        assert result.success is False
        assert result.error == "unknown_mode"


class TestSuggestResources:
    """Tests for the @resource suggest endpoint."""

    @pytest.fixture
    def controller(self):
        from src.platform.resources.base import ResourceSuggestion

        manager = Mock()
        manager.suggest_resources = AsyncMock(return_value=[
            ResourceSuggestion(
                uri="models.lora", label="Lora", kind="model_type",
                has_children=True, icon="box",
            ),
        ])
        return ChatController(chat_runtime=manager, turn_registry=ChatTurnRegistry()), manager

    @pytest.mark.asyncio
    async def test_suggest_resources_success(self, controller):
        ctrl, manager = controller
        user = Mock()
        user.id = "user-1"

        result = await ctrl.suggest_resources("models", "generation", 15, user)

        assert result.success is True
        assert result.data["suggestions"] == [{
            "uri": "models.lora", "label": "Lora", "kind": "model_type",
            "description": None, "has_children": True, "icon": "box",
            "attachable": False,
        }]
        manager.suggest_resources.assert_awaited_once_with(
            query="models", mode_id="generation", user_id="user-1", limit=15,
        )

    @pytest.mark.asyncio
    async def test_suggest_resources_clamps_limit(self, controller):
        ctrl, manager = controller
        user = Mock()
        user.id = "user-1"

        await ctrl.suggest_resources("", None, 500, user)

        assert manager.suggest_resources.call_args.kwargs["limit"] == 50

    @pytest.mark.asyncio
    async def test_suggest_resources_error_wrapped(self, controller):
        ctrl, manager = controller
        manager.suggest_resources.side_effect = RuntimeError("boom")
        user = Mock()
        user.id = "user-1"

        result = await ctrl.suggest_resources("x", None, 15, user)

        assert result.success is False
        assert result.error == "suggest_resources_failed"


class TestMemoryEndpoints:
    """Tests for the persistent LLM memory CRUD endpoints.

    `memory_operations` (as imported into routes.py) is patched to a Mock, so
    tests assert on write_note/read_notes/etc. calls without exercising the
    real validation logic (covered separately by
    tests/features/llm_memory/test_operations.py).
    """

    @pytest.fixture
    def controller(self, monkeypatch):
        chat_runtime = Mock()
        chat_runtime.llm_memory_repository = Mock()
        mock_ops = Mock()
        monkeypatch.setattr("src.features.chat.routes.memory_operations", mock_ops)
        return ChatController(chat_runtime=chat_runtime, turn_registry=ChatTurnRegistry()), mock_ops, chat_runtime.llm_memory_repository

    @pytest.fixture
    def user(self):
        user = Mock()
        user.id = "user-1"
        return user

    def _note(self, **overrides):
        note = Mock()
        note.to_dict.return_value = {
            "id": "note-1", "user_id": "user-1", "key": "pref",
            "content": "likes cinematic lighting", "scope": "global",
            "scope_ref": None, "created_at": None, "updated_at": None,
            **overrides,
        }
        return note

    def test_list_memory_notes(self, controller, user):
        ctrl, memory_advisor, memory_repo = controller
        memory_advisor.read_notes.return_value = [self._note()]

        result = ctrl.list_memory_notes(user, scope="global", scope_ref=None)

        assert result.success is True
        assert result.data["notes"] == [self._note().to_dict.return_value]
        memory_advisor.read_notes.assert_called_once_with(memory_repo, "user-1", scope="global", scope_ref=None)

    def test_list_memory_notes_includes_injection_caps(self, controller, user):
        ctrl, memory_advisor, memory_repo = controller
        memory_advisor.read_notes.return_value = []

        result = ctrl.list_memory_notes(user)

        assert result.data["injection"] == {"cap_per_group": 20, "max_content_len": 500}

    def test_list_memory_notes_no_manager(self, user):
        ctrl = ChatController(chat_runtime=Mock(llm_memory_repository=None), turn_registry=ChatTurnRegistry())

        result = ctrl.list_memory_notes(user)

        assert result.success is False
        assert result.error == "memory_unavailable"

    def test_write_memory_note(self, controller, user):
        from src.features.chat.dto import MemoryWriteRequest

        ctrl, memory_advisor, memory_repo = controller
        memory_advisor.write_note.return_value = self._note()
        request = MemoryWriteRequest(key="pref", content="likes cinematic lighting", scope="global")

        result = ctrl.write_memory_note(request, user)

        assert result.success is True
        assert result.data["key"] == "pref"
        memory_advisor.write_note.assert_called_once_with(
            memory_repo,
            user_id="user-1", key="pref", content="likes cinematic lighting",
            scope="global", scope_ref=None,
        )

    def test_write_memory_note_invalid_scope(self, controller, user):
        from src.features.chat.dto import MemoryWriteRequest

        ctrl, memory_advisor, memory_repo = controller
        memory_advisor.write_note.side_effect = ValueError("Invalid scope 'bogus'")
        request = MemoryWriteRequest(key="pref", content="c", scope="bogus")

        result = ctrl.write_memory_note(request, user)

        assert result.success is False
        assert result.error == "invalid_memory_note"

    def test_update_memory_note(self, controller, user):
        from src.features.chat.dto import MemoryUpdateRequest

        ctrl, memory_advisor, memory_repo = controller
        memory_advisor.update_note.return_value = self._note(key="new_key")
        request = MemoryUpdateRequest(key="new_key", content="updated content")

        result = ctrl.update_memory_note("note-1", request, user)

        assert result.success is True
        memory_advisor.update_note.assert_called_once_with(
            memory_repo,
            user_id="user-1", note_id="note-1", key="new_key", content="updated content",
        )

    def test_update_memory_note_content_too_long(self, controller, user):
        from src.features.chat.dto import MemoryUpdateRequest

        ctrl, memory_advisor, memory_repo = controller
        memory_advisor.update_note.side_effect = ValueError(
            "Memory content is limited to 500 characters - distill the note"
        )
        request = MemoryUpdateRequest(key="pref", content="x" * 501)

        result = ctrl.update_memory_note("note-1", request, user)

        assert result.success is False
        assert result.error == "invalid_memory_note"

    def test_update_memory_note_not_found(self, controller, user):
        from src.features.chat.dto import MemoryUpdateRequest

        ctrl, memory_advisor, memory_repo = controller
        memory_advisor.update_note.return_value = None
        request = MemoryUpdateRequest(key="k", content="c")

        result = ctrl.update_memory_note("missing", request, user)

        assert result.success is False
        assert result.error == "note_not_found"

    def test_delete_memory_note(self, controller, user):
        ctrl, memory_advisor, memory_repo = controller
        memory_advisor.delete_note.return_value = True

        result = ctrl.delete_memory_note("note-1", user)

        assert result.success is True
        memory_advisor.delete_note.assert_called_once_with(memory_repo, user_id="user-1", note_id="note-1")

    def test_delete_memory_note_not_found(self, controller, user):
        ctrl, memory_advisor, memory_repo = controller
        memory_advisor.delete_note.return_value = False

        result = ctrl.delete_memory_note("missing", user)

        assert result.success is False
        assert result.error == "note_not_found"
