from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.chat.context_builder import ChatContextBuilder
from src.features.chat.dto import MessageResponse
from src.features.chat.hooks import CHAT_MESSAGE_HOOKS, CHAT_RESPONSE_HOOKS
from src.features.chat.modes import (
    CONVERSATION_MODE_ID,
    ChatModeRegistry,
    build_conversation_mode,
    build_generation_mode,
)
from src.features.chat.modes.builtin import CONVERSATION_SYSTEM_PROMPT
from src.features.chat.reply_contract import REPLY_CONTRACT_PROMPT_BLOCK, REPLY_CONTRACT_REMINDER
from src.features.chat.routes import ChatController
from src.features.chat.runtime import ChatRuntime
from src.features.chat.turns import ChatTurnRegistry
from src.features.llm.harness.replay import policy_replay
from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.registry import ToolRegistry
from src.platform.resources import ResolvedResource
from src.platform.security.user import AccountType, User
from tests.fixtures.chat_engine import ScriptedTurnEngine, buffered_reply, llm_service_mock, seed_history

_PAGE_CONTEXT = {
    "form_state": {
        "preset": "sdxl-base",
        "mode": "txt2img",
        "form_data": {"prompt": "a castle"},
        "video_director": {"active": False, "doc": None, "capabilities": None},
    },
    "segments": [
        {"index": 0, "id": "seg-1", "type": "content", "enabled": True, "content": "SEGMENT-ON-THE-PAGE"},
    ],
    "prompt_batch": {"active": 0, "prompts": []},
    "image_url": "/api/files/u/cat.png",
}


def _tools() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


def _modes() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    registry.register(build_conversation_mode())
    return registry


class TestConversationModeShape:
    def test_it_is_a_plain_chat_picked_by_hand(self):
        mode = build_conversation_mode()

        assert mode.id == CONVERSATION_MODE_ID == "conversation"
        assert mode.name == "Conversation"
        assert mode.icon == "chat"
        assert mode.source == "builtin"
        assert mode.default_route_prefixes == []
        assert mode.resource_namespaces == []
        assert mode.offers_tools is False
        assert mode.app_context is False
        assert mode.structured_reply is False
        assert mode.context_contributor is None

    def test_other_modes_keep_tools_and_context(self):
        mode = build_generation_mode()

        assert mode.offers_tools is True
        assert mode.app_context is True


class TestConversationToolScope:
    def test_no_tool_reaches_it_not_even_a_global_one(self):
        registry = _tools()
        globals_ = [tool.name for tool in registry.get_all() if tool.modes is None]

        assert "write_memory" in globals_
        assert registry.get_for_mode(build_conversation_mode()) == []

    def test_a_tool_that_names_the_mode_stays_out_too(self):
        registry = _tools()
        tool = registry.get("write_memory")
        tool.modes = [CONVERSATION_MODE_ID]

        assert registry.get_for_mode(build_conversation_mode()) == []

    def test_the_session_prompt_is_the_plain_prompt_with_no_tools(self):
        manager = Mock()
        manager.chat_mode_registry = _modes()
        manager.tool_executor = Mock(tool_registry=_tools())
        manager.tool_governance_repository = None
        builder = ChatContextBuilder(manager)
        session = Mock(mode=CONVERSATION_MODE_ID, metadata=None, llm_config_id="cfg", user_id="u1")

        prompt, allowed, mode, deferred = builder.resolve_session_prompt_and_tools(
            session, form_state=_PAGE_CONTEXT["form_state"],
        )

        assert prompt == CONVERSATION_SYSTEM_PROMPT
        assert REPLY_CONTRACT_PROMPT_BLOCK not in prompt
        assert allowed == []
        assert deferred == {}
        assert builder.withheld_tools_for_session(session) == {}

    def test_the_modes_endpoint_lists_it_with_its_icon_and_no_tools(self):
        manager = Mock()
        manager.chat_mode_registry = _modes()
        manager.tool_executor = Mock(tool_registry=_tools())
        controller = ChatController(chat_runtime=manager, turn_registry=ChatTurnRegistry())
        user = User(username="u", email="u@example.com", password_hash="x", account_type=AccountType.USER, id="u1")

        modes = {mode["id"]: mode for mode in controller.get_modes(user).data["modes"]}

        conversation = modes[CONVERSATION_MODE_ID]
        assert conversation["name"] == "Conversation"
        assert conversation["icon"] == "chat"
        assert conversation["tools"] == []
        assert conversation["offers_tools"] is False
        assert conversation["app_context"] is False
        assert "write_memory" in modes["generation"]["tools"]
        assert modes["generation"]["offers_tools"] is True


class _Turn:
    def __init__(self, mode_id: str, history_budget_tokens: int = 0):
        self.engine = ScriptedTurnEngine()
        reply = Mock(
            content="Happy to chat.", model="m", tokens_used=3, prompt_tokens=2, completion_tokens=1,
            rescues=None, tool_failures=None, thinking_mode=None,
        )
        self.engine.script(buffered_reply(reply))

        self.repo = Mock()
        self.repo.get_session.return_value = Mock(
            user_id="user-1", status="active", llm_config_id="cfg-1", mode=mode_id, metadata=None,
        )
        self.repo.add_message.side_effect = [
            MessageResponse(id="msg-1", session_id="s1", role="user", content="hi"),
            MessageResponse(id="msg-2", session_id="s1", role="assistant", content="Happy to chat."),
        ]

        self.plugins = Mock()
        hook_context = Mock()
        hook_context.data = {}
        self.plugins.execute_hook.return_value = (hook_context, [])

        processor = Mock()
        processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        settings = Mock()
        settings.get_setting = Mock(return_value=history_budget_tokens)

        self.pre_chat = Mock()
        self.pre_chat.get_enabled_actions.return_value = []
        self.pre_chat.execute_actions = AsyncMock(return_value=[])

        self.resources = Mock()
        self.resources.resolve = AsyncMock(return_value=ResolvedResource(
            uri="@form:prompt", namespace="form", kind="form", title="Prompt", content="RESOURCE-FROM-THE-PAGE",
        ))

        self.runtime = ChatRuntime(
            turn_engine=self.engine,
            replay_selector=policy_replay,
            chat_repository=self.repo,
            llm_service=llm_service_mock(),
            response_processor=processor,
            plugin_registry=self.plugins,
            chat_mode_registry=_modes(),
            tool_executor=Mock(tool_registry=_tools()),
            pre_chat_action_registry=self.pre_chat,
            resource_registry=self.resources,
            tool_services=ToolServices(llm_memory_repository=Mock(), settings=settings),
        )
        self.runtime.reflection_generator = Mock()

    async def send(self, **overrides):
        values = dict(
            session_id="s1", user_id="user-1", content="hi",
            context_metadata=_PAGE_CONTEXT, resources=["@form:prompt"],
        )
        values.update(overrides)
        with patch("src.features.chat.context_builder.memory_operations") as memory:
            note = Mock(id="note-1", key="pref", content="RECALLED-MEMORY-NOTE")
            memory.read_notes.side_effect = lambda repo, user_id, scope, scope_ref=None: [note] if scope == "global" else []
            await self.runtime.send_message(**values)
        return memory

    @property
    def sent(self):
        return self.engine.inputs[-1]

    @property
    def sent_text(self):
        return "\n".join(message["content"] or "" for message in self.engine.call_kwargs["messages"])

    @property
    def trace(self):
        return self.repo.add_message.call_args_list[1][1]["metadata"]["behavior_trace"]

    @property
    def hooks(self):
        return [call.args[0] for call in self.plugins.execute_hook.call_args_list]


class TestConversationTurn:
    @pytest.mark.asyncio
    async def test_the_model_gets_the_plain_prompt_and_no_tools(self):
        turn = _Turn(CONVERSATION_MODE_ID)

        await turn.send()

        assert turn.sent.system == CONVERSATION_SYSTEM_PROMPT
        assert turn.sent.allowed_tools == ()
        assert dict(turn.sent.deferred_tools) == {}
        assert turn.sent.tool_context is None
        assert turn.sent.mode == CONVERSATION_MODE_ID
        assert turn.trace["tools_offered"] == []
        assert turn.trace["tools_withheld"] == {}

    @pytest.mark.asyncio
    async def test_page_context_the_frontend_sends_is_never_injected(self):
        turn = _Turn(CONVERSATION_MODE_ID)

        memory = await turn.send()

        assert turn.engine.call_kwargs["messages"][-1] == {"role": "user", "content": "hi"}
        assert "<context>" not in turn.sent_text
        assert "SEGMENT-ON-THE-PAGE" not in turn.sent_text
        assert "RESOURCE-FROM-THE-PAGE" not in turn.sent_text
        assert "PROMPT STATE" not in turn.sent_text
        assert "RECALLED-MEMORY-NOTE" not in turn.sent_text
        assert REPLY_CONTRACT_REMINDER not in turn.sent_text
        memory.read_notes.assert_not_called()
        turn.resources.resolve.assert_not_called()
        turn.runtime.reflection_generator.trigger.assert_not_called()
        assert turn.trace["memory"]["injected_chars"] == 0

    @pytest.mark.asyncio
    async def test_an_attached_image_still_reaches_the_user_message(self):
        turn = _Turn(CONVERSATION_MODE_ID)

        await turn.send()

        user_metadata = turn.repo.add_message.call_args_list[0][1]["metadata"]
        assert user_metadata == {"image_url": "/api/files/u/cat.png"}

    @pytest.mark.asyncio
    async def test_the_chat_hooks_and_pre_chat_actions_still_run(self):
        turn = _Turn(CONVERSATION_MODE_ID)

        await turn.send()

        assert turn.hooks == [
            CHAT_MESSAGE_HOOKS.before_send,
            CHAT_RESPONSE_HOOKS.before_generate,
            CHAT_RESPONSE_HOOKS.after_save,
            CHAT_MESSAGE_HOOKS.after_send,
        ]
        turn.pre_chat.execute_actions.assert_awaited_once_with("cfg-1")

    @pytest.mark.asyncio
    async def test_a_long_chat_is_still_trimmed_to_the_history_budget(self):
        turn = _Turn(CONVERSATION_MODE_ID, history_budget_tokens=10)
        seed_history(turn.engine.store, "s1", [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"old turn {i} " + "x" * 60}
            for i in range(6)
        ])

        await turn.send()

        assert "old turn" not in turn.sent_text
        assert turn.trace["history"]["truncated"] is True
        assert turn.trace["history"]["messages_total"] == 7
        assert turn.engine.call_kwargs["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_the_same_send_in_a_generation_chat_does_carry_the_page(self):
        turn = _Turn("generation")

        memory = await turn.send()

        assert "SEGMENT-ON-THE-PAGE" in turn.sent_text
        assert "RESOURCE-FROM-THE-PAGE" in turn.sent_text
        assert "RECALLED-MEMORY-NOTE" in turn.sent_text
        assert "write_memory" in turn.sent.allowed_tools
        memory.read_notes.assert_called()
        turn.resources.resolve.assert_awaited()
        turn.runtime.reflection_generator.trigger.assert_called_once()
