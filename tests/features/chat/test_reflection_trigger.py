"""Integration of the reflection trigger seam into ConversationRunner: fires
as a background task after a turn, never awaited by the response path, and
only actually calls the LLM once its own gating conditions are met."""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch

from src.features.chat.dto import MessageResponse
from src.features.chat.runtime import ChatRuntime
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.chat.reflection import MIN_UNREFLECTED_USER_MESSAGES


def _mode_registry() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


def _message(role: str, content: str, msg_id: str) -> Mock:
    msg = Mock()
    msg.id = msg_id
    msg.role = role
    msg.content = content
    return msg


def _transcript(user_count: int) -> list:
    out = []
    for i in range(user_count):
        out.append(_message("user", f"q{i}", f"u{i}"))
        out.append(_message("assistant", f"a{i}", f"a{i}"))
    return out


class TestReflectionFiresOnSend:
    def setup_method(self):
        self.mock_repo = Mock()
        self.mock_llm = Mock()
        self.mock_processor = Mock()
        self.mock_plugins = Mock()
        self.mock_memory = Mock()

        no_block_ctx = Mock()
        no_block_ctx.data = {}
        self.mock_plugins.execute_hook.return_value = (no_block_ctx, [])
        self.mock_processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        self.manager = ChatRuntime(
            chat_repository=self.mock_repo,
            llm_service=self.mock_llm,
            response_processor=self.mock_processor,
            plugin_registry=self.mock_plugins,
            chat_mode_registry=_mode_registry(),
        )
        # Late-bound the same way the composition root does.
        self.manager.llm_memory_repository = self.mock_memory

        # `memory_operations` (as imported into reflection.py) is patched to a
        # Mock so tests can assert on write_note calls without exercising the
        # real validation logic - covered separately by
        # tests/features/llm_memory/test_operations.py.
        self._memory_ops_patcher = patch("src.features.chat.reflection.memory_operations")
        self.mock_memory_ops = self._memory_ops_patcher.start()

        session = Mock()
        session.id = "session-1"
        session.user_id = "user-1"
        session.status = "active"
        session.mode = "generation"
        session.llm_config_id = "llm-1"
        session.metadata = {"enabled_tools": []}

        self.mock_repo.get_session.return_value = session
        self.mock_repo.add_message.side_effect = lambda **kwargs: MessageResponse(
            id="msg-1", session_id="session-1", role=kwargs.get("role", "assistant"),
            content=kwargs.get("content", ""),
        )
        self.mock_repo.get_conversation_history.return_value = [{"role": "user", "content": "hi"}]
        self.mock_repo.count_messages.return_value = 2
        # title_generator needs >=2 messages; irrelevant to this test beyond not erroring.
        self.mock_repo.get_messages.return_value = _transcript(MIN_UNREFLECTED_USER_MESSAGES)
        self.mock_repo.set_session_title.return_value = session
        self.mock_repo.record_memory_reflection.return_value = True

        config = Mock()
        config.memory_reflection = True
        self.mock_llm.repository = Mock()
        self.mock_llm.repository.get_configuration.return_value = config

        self.mock_llm.generate_response = AsyncMock(return_value=Mock(content="ack", tokens_used=1))
        title_response = Mock()
        title_response.content = "A Title"
        reflection_response = Mock()
        reflection_response.content = '[{"scope": "global", "key": "k", "content": "prefers moody lighting over bright scenes"}]'
        self.mock_llm.generate_with_history = AsyncMock(side_effect=[title_response, reflection_response])

        saved_note = Mock()
        saved_note.to_dict.return_value = {"key": "k"}
        self.mock_memory_ops.write_note.return_value = saved_note

    def teardown_method(self):
        self._memory_ops_patcher.stop()

    async def _drain_background_tasks(self):
        tasks = list(self.manager._reflection_tasks)
        if tasks:
            await asyncio.gather(*tasks)

    @pytest.mark.asyncio
    async def test_response_returns_without_waiting_for_reflection(self):
        # generate_with_history is used by BOTH title generation and reflection;
        # make it hang so a response that waited on it would never return.
        hang = asyncio.Event()

        async def _hanging_call(*args, **kwargs):
            await hang.wait()
            return Mock(content="[]")

        # Title still needs to resolve for send_message to complete; only block
        # the SECOND call (reflection) so a real block there would timeout us.
        calls = {"n": 0}

        async def _side_effect(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return Mock(content="A Title")
            return await _hanging_call()

        self.mock_llm.generate_with_history = AsyncMock(side_effect=_side_effect)

        response = await asyncio.wait_for(
            self.manager.send_message("session-1", "user-1", "hello"),
            timeout=5,
        )

        assert response is not None
        hang.set()
        await self._drain_background_tasks()

    @pytest.mark.asyncio
    async def test_reflection_task_persists_extracted_note(self):
        await self.manager.send_message("session-1", "user-1", "hello")
        await self._drain_background_tasks()

        self.mock_memory_ops.write_note.assert_called_once_with(
            self.mock_memory,
            user_id="user-1", key="k",
            content="prefers moody lighting over bright scenes",
            scope="global", scope_ref=None,
        )
        self.mock_repo.record_memory_reflection.assert_called_once()

    @pytest.mark.asyncio
    async def test_toggle_off_skips_reflection_entirely(self):
        self.mock_llm.repository.get_configuration.return_value.memory_reflection = False

        await self.manager.send_message("session-1", "user-1", "hello")
        await self._drain_background_tasks()

        self.mock_memory_ops.write_note.assert_not_called()
        self.mock_repo.record_memory_reflection.assert_not_called()

    @pytest.mark.asyncio
    async def test_below_threshold_skips_reflection(self):
        self.mock_repo.get_messages.return_value = _transcript(MIN_UNREFLECTED_USER_MESSAGES - 1)

        await self.manager.send_message("session-1", "user-1", "hello")
        await self._drain_background_tasks()

        self.mock_memory_ops.write_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_reflected_span_does_not_double_reflect(self):
        """Bookkeeping (reflected_up_to_message_id) prevents a second pass over
        the same span until enough NEW user messages arrive."""
        messages = _transcript(MIN_UNREFLECTED_USER_MESSAGES)
        self.mock_repo.get_session.return_value.metadata = {
            "enabled_tools": [],
            "memory_reflection": {"reflected_up_to_message_id": messages[-2].id},
        }
        self.mock_repo.get_messages.return_value = messages

        await self.manager.send_message("session-1", "user-1", "hello")
        await self._drain_background_tasks()

        self.mock_memory_ops.write_note.assert_not_called()
