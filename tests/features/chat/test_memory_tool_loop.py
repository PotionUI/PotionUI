"""Regression tests: the chat tool loop must not go silent after write_memory.

Ollama's buffered tool-calling call (OllamaClient.generate_with_tools) retries
when a decision call comes back with neither content nor tool_calls — a
documented, non-deterministic empty-completion failure mode for local models.
The streaming tool loop (ToolExecutor.execute_with_tools_stream) had no such
retry: an empty presentation call after a tool executed was finalized
immediately as the turn's answer, so the assistant message came back blank
and the user had to send another message to get a real reply. Models are
disproportionately likely to answer empty right after write_memory, since its
own tool description tells them the save "saves immediately, no approval
needed" — there's nothing left they're obligated to say.

These tests exercise the real ToolRegistry + ToolExecutor (not a mocked tool
executor) through ChatManager.send_message_stream, driven by a fake LLM
service that scripts exactly what a flaky local model does: a tool call, then
a genuinely empty completion, then a real answer on retry.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.chat.manager import ChatManager
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.llm.tools.builtin.memory_tool import WriteMemoryTool
from src.features.llm.tools.executor import ToolExecutor
from src.features.llm.tools.registry import ToolRegistry


def _mode_registry() -> ChatModeRegistry:
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


def _wire_repo(repo, content):
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


def make_memory_manager():
    manager = Mock()
    manager.write_note.return_value = SimpleNamespace(id="note-1", key="lighting_pref", scope="global")
    manager.read_notes.return_value = []
    return manager


class ScriptedLLMService:
    """Fake LLM service whose ``stream_with_tools`` plays back one event list
    per call. Calls beyond the scripted list repeat the last entry.
    """

    def __init__(self, scripts):
        self.repository = Mock()
        self.repository.get_configuration.return_value = SimpleNamespace(provider_options={})
        self.scripts = scripts
        self.call_count = 0

    async def stream_with_tools(self, **kwargs):
        idx = min(self.call_count, len(self.scripts) - 1)
        self.call_count += 1
        for event in self.scripts[idx]:
            yield event


def make_memory_chat_manager(scripts):
    repo = Mock()
    processor = Mock()
    processor.process.side_effect = lambda content, mode=None: (content, None)
    plugins = Mock()
    plugins.execute_hook.return_value = (Mock(data={}), [])

    registry = ToolRegistry()
    registry.register(WriteMemoryTool())
    llm_service = ScriptedLLMService(scripts)
    executor = ToolExecutor(tool_registry=registry, llm_service=llm_service)

    manager = ChatManager(
        chat_repository=repo,
        llm_service=llm_service,
        response_processor=processor,
        plugin_registry=plugins,
        chat_mode_registry=_mode_registry(),
        tool_executor=executor,
        llm_memory_manager=make_memory_manager(),
    )
    return manager, repo, llm_service


WRITE_MEMORY_CALL = {
    "id": "call-1",
    "function": {
        "name": "write_memory",
        "arguments": json.dumps({
            "key": "lighting_pref", "content": "likes moody lighting", "scope": "global",
        }),
    },
}


class TestMemorySaveLoopContinuation:
    @pytest.mark.asyncio
    async def test_follow_up_survives_an_empty_completion_after_write_memory(self):
        """An empty post-tool completion must trigger a retry, not end the turn."""
        scripts = [
            [{"type": "tool_calls", "tool_calls": [WRITE_MEMORY_CALL]}],
            [],  # the flaky-model failure mode: no tokens, no tool_calls
            [
                {"type": "token", "content": "Got it, I'll remember that."},
                {"type": "usage", "tokens_used": 10, "prompt_tokens": 5, "completion_tokens": 5},
            ],
        ]
        manager, repo, llm = make_memory_chat_manager(scripts)
        saved = _wire_repo(repo, "Remember that I like moody lighting.")

        events = []
        async for event in manager.send_message_stream(
            session_id="session-1", user_id="user-1",
            content="Remember that I like moody lighting.",
        ):
            events.append(event)

        assert not [e for e in events if e["event"] == "error"]
        done = next(e for e in events if e["event"] == "done")
        assert done["data"]["assistant_message"]["content"] == "Got it, I'll remember that."
        assert saved["content"] == "Got it, I'll remember that."
        # tool decision call + empty attempt + retry
        assert llm.call_count == 3

    @pytest.mark.asyncio
    async def test_normal_follow_up_after_write_memory_is_unaffected(self):
        """A normal (non-empty) presentation call is untouched by the retry path."""
        scripts = [
            [{"type": "tool_calls", "tool_calls": [WRITE_MEMORY_CALL]}],
            [
                {"type": "token", "content": "Noted."},
                {"type": "usage", "tokens_used": 8, "prompt_tokens": 4, "completion_tokens": 4},
            ],
        ]
        manager, repo, llm = make_memory_chat_manager(scripts)
        saved = _wire_repo(repo, "Remember that I like moody lighting.")

        events = []
        async for event in manager.send_message_stream(
            session_id="session-1", user_id="user-1",
            content="Remember that I like moody lighting.",
        ):
            events.append(event)

        done = next(e for e in events if e["event"] == "done")
        assert done["data"]["assistant_message"]["content"] == "Noted."
        assert saved["content"] == "Noted."
        assert llm.call_count == 2

    @pytest.mark.asyncio
    async def test_repeated_empty_completions_still_terminate_the_turn(self):
        """If every retry also comes back empty, the loop must give up cleanly
        (empty final answer, no hang, no error) rather than retry forever."""
        scripts = [
            [{"type": "tool_calls", "tool_calls": [WRITE_MEMORY_CALL]}],
            [],
        ]
        manager, repo, llm = make_memory_chat_manager(scripts)
        saved = _wire_repo(repo, "Remember that I like moody lighting.")

        events = []
        async for event in manager.send_message_stream(
            session_id="session-1", user_id="user-1",
            content="Remember that I like moody lighting.",
        ):
            events.append(event)

        assert not [e for e in events if e["event"] == "error"]
        assert [e for e in events if e["event"] == "done"]
        assert saved["content"] == ""
        # Bounded retries — must not call the LLM an unbounded number of times.
        assert 1 < llm.call_count <= 5
