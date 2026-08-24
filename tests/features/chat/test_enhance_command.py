"""Tests for prompt enhancement as an ordinary chat tool.

Prompt enhancement is no longer a slash command that bypasses the LLM tool
loop. ``enhance_prompt`` is a regular tool: the model calls it like any other
tool when the user asks to improve/expand a prompt, and the ordinary tool loop
runs it and lets the model present the result. These tests exercise that
routing end-to-end through a real ToolRegistry + ToolExecutor with a fake LLM,
plus confirm typing "/enhance" literally is no longer special-cased.
"""

import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from src.features.chat.manager import ChatManager
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.chat.exceptions import MessageCreationFailedException
from src.features.llm.tools.registry import ToolRegistry
from src.features.llm.tools.executor import ToolExecutor
from src.features.llm.tools.builtin.enhance_prompt_tool import EnhancePromptTool


def _mode_registry() -> ChatModeRegistry:
    """Real mode registry with the builtin generation mode (no settings)."""
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


def make_session(**overrides):
    session = Mock()
    session.user_id = "user-1"
    session.status = "active"
    session.llm_config_id = "llm-1"
    session.mode = "generation"
    session.metadata = {}
    for k, v in overrides.items():
        setattr(session, k, v)
    return session


def make_message(id_, content="", metadata=None, session_id="session-1"):
    message = Mock()
    message.id = id_
    message.content = content
    message.metadata = metadata
    message.session_id = session_id
    message.model_dump.return_value = {"id": id_, "content": content}
    return message


def make_enhancement_manager(candidates=None):
    manager = MagicMock()
    result = {
        "candidates": [{"text": c, "direction": ""} for c in (candidates or ["rich prompt one", "rich prompt two"])],
        "model_id": "model-1",
        "brief": "a fox",
        "exemplar_ids": ["e-1"],
    }
    manager.enhance = AsyncMock(return_value=result)
    manager.record_feedback = AsyncMock(return_value={"feedback_id": "fb-1", "prompt_id": None})
    return manager


class FakeLLMService:
    """Minimal LLM service for driving a real ToolExecutor in tests.

    ``turns`` describes the model's successive responses: a turn with
    ``tool_calls`` set drives a model-chosen tool call; a turn with only
    ``text`` ends the loop with that presentation text. Defaults to a single
    text-only turn (no tool calls), matching an ordinary chat reply.
    """

    def __init__(self, turns=None):
        self.turns = turns if turns is not None else [{"text": "here you go"}]
        self._n = 0
        # get_configuration is read by _force_prompt_tools_for; empty options
        # keeps the native (non-force_prompt_tools) streaming path.
        self.repository = Mock()
        self.repository.get_configuration.return_value = SimpleNamespace(provider_options={})
        self.stream_calls: list = []
        self.generate_calls: list = []

    def _next_turn(self):
        turn = self.turns[min(self._n, len(self.turns) - 1)]
        self._n += 1
        return turn

    async def stream_with_tools(self, **kwargs):
        self.stream_calls.append(kwargs)
        turn = self._next_turn()
        if turn.get("tool_calls"):
            yield {"type": "tool_calls", "tool_calls": turn["tool_calls"]}
            yield {"type": "usage", "tokens_used": None, "prompt_tokens": None, "completion_tokens": None}
        else:
            yield {"type": "token", "content": turn.get("text", "")}
            yield {"type": "usage", "tokens_used": 10, "prompt_tokens": 5, "completion_tokens": 5}

    async def generate_with_tools(self, **kwargs):
        self.generate_calls.append(kwargs)
        turn = self._next_turn()
        resp = MagicMock()
        resp.content = turn.get("text", "")
        resp.tool_calls = turn.get("tool_calls") or []
        resp.tokens_used = 10
        resp.prompt_tokens = 5
        resp.completion_tokens = 5
        return resp


def make_chat_manager(enhancement_manager=None):
    """ChatManager without a tool executor — for record_prompt_feedback tests."""
    repo = Mock()
    processor = Mock()
    processor.process.side_effect = lambda content, mode=None: (content, None)
    plugins = Mock()
    plugins.execute_hook.return_value = (Mock(data={}), [])

    manager = ChatManager(
        chat_repository=repo,
        llm_service=Mock(),
        response_processor=processor,
        plugin_registry=plugins,
        chat_mode_registry=_mode_registry(),
        prompt_enhancement_manager=enhancement_manager or make_enhancement_manager(),
    )
    return manager, repo


def make_tool_chat_manager(enhancement_manager=None, turns=None):
    """ChatManager wired with a real registry + executor and a fake LLM.

    Exercises the real tool routing: the fake LLM's turns decide whether
    enhance_prompt gets called (model-chosen) or the turn is a plain reply.
    """
    repo = Mock()
    processor = Mock()
    processor.process.side_effect = lambda content, mode=None: (content, None)
    plugins = Mock()
    plugins.execute_hook.return_value = (Mock(data={}), [])

    registry = ToolRegistry()
    registry.register(EnhancePromptTool())
    llm_service = FakeLLMService(turns)
    executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

    manager = ChatManager(
        chat_repository=repo,
        llm_service=llm_service,
        response_processor=processor,
        plugin_registry=plugins,
        chat_mode_registry=_mode_registry(),
        tool_executor=executor,
        prompt_enhancement_manager=enhancement_manager or make_enhancement_manager(),
    )
    return manager, repo, llm_service


SEGMENTS_METADATA = {
    "segments": [
        {"index": 0, "id": "seg-1", "content": "a fox", "type": "content", "isDisabled": False},
        {"index": 1, "id": "seg-2", "content": "blurry", "type": "negative", "isDisabled": False},
    ],
    "form_state": {"preset": "p-1", "mode": "txt2img", "form_data": {}},
}


def _wire_repo(repo, content):
    """Wire chat_repository so add_message returns messages and captures the assistant save."""
    session = make_session()
    repo.get_session.return_value = session
    repo.get_conversation_history.return_value = [{"role": "user", "content": content}]
    repo.count_messages.return_value = 2
    user_message = make_message("um-1", content)
    saved = {}

    def add_message(**kwargs):
        if kwargs["role"] == "user":
            return user_message
        saved.update(kwargs)
        return make_message("am-1", kwargs["content"], kwargs.get("metadata"))

    repo.add_message.side_effect = add_message
    return saved


ENHANCE_TOOL_CALL = [{
    "id": "call-1",
    "function": {"name": "enhance_prompt", "arguments": json.dumps({"brief": "a fox"})},
}]


class TestEnhancePromptToolStream:
    """The model chooses enhance_prompt itself, mid-conversation, like any other tool."""

    async def _run(self, manager, repo, content="make my prompt richer", context_metadata=SEGMENTS_METADATA):
        saved = _wire_repo(repo, content)
        events = []
        async for event in manager.send_message_stream(
            session_id="session-1", user_id="user-1", content=content,
            context_metadata=context_metadata,
        ):
            events.append(event)
        return events, saved

    @pytest.mark.asyncio
    async def test_model_chosen_call_runs_enhancement_and_presents(self):
        manager, repo, llm = make_tool_chat_manager(
            turns=[{"tool_calls": ENHANCE_TOOL_CALL}, {"text": "here's a richer version"}]
        )
        events, saved = await self._run(manager, repo)

        manager.prompt_enhancement_manager.enhance.assert_awaited_once()
        call = manager.prompt_enhancement_manager.enhance.await_args.kwargs
        assert call["brief"] == "a fox"

        tool_starts = [e for e in events if e["event"] == "tool_start"]
        assert any(e["data"]["tool_name"] == "enhance_prompt" for e in tool_starts)
        assert events[-1]["event"] == "done"

        tool_execs = saved["metadata"].get("tool_executions") or []
        exec_data = next(te for te in tool_execs if te["tool_name"] == "enhance_prompt")
        # The Apply-able payload: the enhanced prompt plus the presentation
        # instruction, unchanged whether the tool is model-chosen or (formerly)
        # slash-forced. The instruction wraps it in the update_segment
        # <tool_action> tag, never printed as plain reply text.
        payload = json.loads(exec_data["result"]["data"])
        assert payload["enhanced_prompt"] == "rich prompt one"
        assert 'tool_action type="update_segment"' in payload["instruction"]

    @pytest.mark.asyncio
    async def test_plain_message_does_not_call_enhancement(self):
        manager, repo, llm = make_tool_chat_manager()
        await self._run(manager, repo, content="what model should I use?")

        manager.prompt_enhancement_manager.enhance.assert_not_awaited()


class TestSlashEnhanceIsPlainText:
    """/enhance is no longer a special command; it reaches the model as ordinary text."""

    async def _run(self, manager, repo, content):
        saved = _wire_repo(repo, content)
        events = []
        async for event in manager.send_message_stream(
            session_id="session-1", user_id="user-1", content=content,
            context_metadata=SEGMENTS_METADATA,
        ):
            events.append(event)
        return events, saved

    @pytest.mark.asyncio
    async def test_slash_enhance_does_not_force_the_tool(self):
        manager, repo, llm = make_tool_chat_manager()
        events, saved = await self._run(manager, repo, "/enhance a lonely lighthouse")

        manager.prompt_enhancement_manager.enhance.assert_not_awaited()
        assert not any(e["event"] == "tool_start" for e in events)
        # The literal text was sent to the model like any other message.
        sent = llm.stream_calls[0]["messages"]
        assert any(m.get("content") == "/enhance a lonely lighthouse" for m in sent)

    @pytest.mark.asyncio
    async def test_bare_slash_enhance_does_not_force_the_tool(self):
        manager, repo, llm = make_tool_chat_manager()
        await self._run(manager, repo, "/enhance")

        manager.prompt_enhancement_manager.enhance.assert_not_awaited()


class TestEnhancePromptToolNonStreaming:
    @pytest.mark.asyncio
    async def test_model_chosen_call_runs_enhancement_buffered(self):
        from src.features.chat.dto import MessageResponse

        manager, repo, llm = make_tool_chat_manager(
            turns=[{"tool_calls": ENHANCE_TOOL_CALL}, {"text": "here's a richer version"}]
        )
        repo.get_session.return_value = make_session()
        repo.get_conversation_history.return_value = [{"role": "user", "content": "make it richer"}]
        repo.count_messages.return_value = 2
        saved = {}

        def add_message(**kwargs):
            message_id = "um-1" if kwargs["role"] == "user" else "am-1"
            if kwargs["role"] != "user":
                saved.update(kwargs)
            return MessageResponse(
                id=message_id, session_id="session-1",
                role=kwargs["role"], content=kwargs["content"],
                metadata=kwargs.get("metadata"),
            )

        repo.add_message.side_effect = add_message

        response = await manager.send_message(
            session_id="session-1", user_id="user-1", content="make it richer",
            context_metadata=SEGMENTS_METADATA,
        )

        manager.prompt_enhancement_manager.enhance.assert_awaited_once()
        assert llm.generate_calls, "buffered tool loop should call the LLM to present"
        tool_execs = saved["metadata"].get("tool_executions") or []
        assert any(te["tool_name"] == "enhance_prompt" for te in tool_execs)
        assert response.assistant_message.id == "am-1"


class TestRecordPromptFeedback:
    def _setup(self, message):
        manager, repo = make_chat_manager()
        repo.get_session.return_value = make_session()
        repo.get_message.return_value = message
        repo.update_message_metadata.return_value = True
        return manager, repo

    @pytest.mark.asyncio
    async def test_approves_candidate_from_enhancement_metadata(self):
        message = make_message("am-1", metadata={
            "enhancement": {"candidates": ["first", "second"], "model_id": "model-1"},
        })
        manager, repo = self._setup(message)

        result = await manager.record_prompt_feedback(
            session_id="session-1", user_id="user-1", message_id="am-1",
            action_index=1, verdict="approved",
        )

        call = manager.prompt_enhancement_manager.record_feedback.await_args.kwargs
        assert call["prompt_text"] == "second"
        assert call["model_id"] == "model-1"
        assert result["verdict"] == "approved"
        persisted = repo.update_message_metadata.call_args.args[1]
        assert persisted["prompt_feedback"]["1"]["verdict"] == "approved"

    @pytest.mark.asyncio
    async def test_falls_back_to_parsing_tool_actions(self):
        content = (
            'intro\n\n<tool_action type="update_segment" segment_index="0" '
            'segment_id="seg-1">parsed prompt</tool_action>'
        )
        message = make_message("am-1", content=content, metadata=None)
        manager, repo = self._setup(message)

        await manager.record_prompt_feedback(
            session_id="session-1", user_id="user-1", message_id="am-1",
            action_index=0, verdict="rejected", reason="too plain",
        )

        call = manager.prompt_enhancement_manager.record_feedback.await_args.kwargs
        assert call["prompt_text"] == "parsed prompt"
        assert call["reason"] == "too plain"

    @pytest.mark.asyncio
    async def test_falls_back_to_parsing_director_segment_tool_actions(self):
        content = (
            'intro\n\n<tool_action type="update_director_segment" segment_index="0" '
            'segment_id="seg-1">parsed director prompt</tool_action>'
        )
        message = make_message("am-1", content=content, metadata=None)
        manager, repo = self._setup(message)

        await manager.record_prompt_feedback(
            session_id="session-1", user_id="user-1", message_id="am-1",
            action_index=0, verdict="approved",
        )

        call = manager.prompt_enhancement_manager.record_feedback.await_args.kwargs
        assert call["prompt_text"] == "parsed director prompt"

    @pytest.mark.asyncio
    async def test_out_of_range_index_raises(self):
        message = make_message("am-1", metadata={"enhancement": {"candidates": ["only"]}})
        manager, repo = self._setup(message)

        with pytest.raises(MessageCreationFailedException):
            await manager.record_prompt_feedback(
                session_id="session-1", user_id="user-1", message_id="am-1",
                action_index=5, verdict="approved",
            )

    @pytest.mark.asyncio
    async def test_invalid_verdict_raises(self):
        message = make_message("am-1", metadata={"enhancement": {"candidates": ["only"]}})
        manager, repo = self._setup(message)

        with pytest.raises(MessageCreationFailedException):
            await manager.record_prompt_feedback(
                session_id="session-1", user_id="user-1", message_id="am-1",
                action_index=0, verdict="maybe",
            )
