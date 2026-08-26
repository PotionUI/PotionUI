"""Tests for the tool-approval lifecycle.

Covers the two reworked approval behaviours:

- Resolving a pending approval (approve or deny) must *continue* the
  conversation — feed the outcome back and persist a new assistant message that
  narrates what happened, rather than dead-ending after the tool runs.
- Approval-gated tools carry a structured `preview` (action/target/items) on the
  ToolResult their preview `execute()` returns, so the UI can state intent
  instead of dumping raw arguments.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.features.chat.manager import ChatManager
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.llm.tools.base import BaseTool, ToolApprovalPreview, ToolResult
from src.features.llm.tools.executor import ToolExecutor, _ToolCallGuard
from src.features.llm.tools.registry import ToolRegistry


def _mode_registry() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


class _FakeApprovalTool(BaseTool):
    """Minimal approval-gated tool: preview on execute, apply on confirm."""

    modes = ["generation"]

    def __init__(self):
        self.confirmed_with = None

    @property
    def name(self) -> str:
        return "remove_stuff"

    @property
    def description(self) -> str:
        return "Remove some stuff (test tool)."

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    @property
    def requires_approval(self) -> bool:
        return True

    async def execute(self, context, **kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            data=json.dumps({"status": "pending_approval", "count": 2}),
            preview=ToolApprovalPreview(action="Remove", target="from category camera", items=["a", "b"]),
        )

    async def execute_confirmed(self, context, **kwargs) -> ToolResult:
        self.confirmed_with = kwargs
        return ToolResult(success=True, data=json.dumps({"deleted_count": 2}))


class _FakeUpdatePhrasebookTool(BaseTool):
    """Approval-gated tool named exactly like the real
    `update_phrasebook_values` tool, so the per-known-tool fallback
    narration (keyed off the literal tool name) is exercised end to end.
    """

    modes = ["generation"]

    @property
    def name(self) -> str:
        return "update_phrasebook_values"

    @property
    def description(self) -> str:
        return "Update phrasebook values (test tool)."

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    @property
    def requires_approval(self) -> bool:
        return True

    async def execute(self, context, **kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            data=json.dumps({"status": "pending_approval"}),
            preview=ToolApprovalPreview(action="Update", target="camera", items=["a", "b", "c"]),
        )

    async def execute_confirmed(self, context, **kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            data=json.dumps({"updated_count": 3, "values": ["a", "b", "c"]}),
        )


class _PresentingLLM:
    """LLM double that returns a fixed presentation text for the continuation."""

    def __init__(self, text="Done — I removed 2 values from camera."):
        self.text = text
        self.repository = Mock()
        self.repository.get_configuration.return_value = SimpleNamespace(provider_options={})
        self.generate_calls = []

    async def generate_with_tools(self, **kwargs):
        self.generate_calls.append(kwargs)
        return SimpleNamespace(
            content=self.text, tool_calls=[], model="m", tokens_used=7, prompt_tokens=4, completion_tokens=3
        )


class _AlwaysEmptyLLM:
    """LLM double simulating a small model that never produces narratable text.

    Every attempt (including retries) returns a bare/empty completion, so
    `strip_tool_call_xml` reduces it to "" on every call.
    """

    def __init__(self, content=""):
        self.content = content
        self.repository = Mock()
        self.repository.get_configuration.return_value = SimpleNamespace(provider_options={})
        self.generate_calls = []

    async def generate_with_tools(self, **kwargs):
        self.generate_calls.append(kwargs)
        return SimpleNamespace(
            content=self.content, tool_calls=[], model="m", tokens_used=1, prompt_tokens=1, completion_tokens=0
        )


def _make_manager(tool, llm=None):
    repo = Mock()
    processor = Mock()
    processor.process.side_effect = lambda content, mode=None: ((content or "").strip(), None)
    plugins = Mock()
    plugins.execute_hook.return_value = (Mock(data={}), [])

    registry = ToolRegistry()
    registry.register(tool)
    llm = llm if llm is not None else _PresentingLLM()
    executor = ToolExecutor(tool_registry=registry, llm_service=llm)

    manager = ChatManager(
        chat_repository=repo,
        llm_service=llm,
        response_processor=processor,
        plugin_registry=plugins,
        chat_mode_registry=_mode_registry(),
        tool_executor=executor,
    )
    return manager, repo, llm


def _wire_repo(repo, tool_name="remove_stuff"):
    session = Mock()
    session.id = "session-1"
    session.user_id = "user-1"
    session.status = "active"
    session.llm_config_id = "llm-1"
    session.mode = "generation"
    session.metadata = {}
    repo.get_session.return_value = session

    message = Mock()
    message.session_id = "session-1"
    message.metadata = {
        "tool_executions": [{
            "tool_name": tool_name,
            "arguments": {"value_ids": ["1", "2"]},
            "pending_approval": True,
            "result": {"success": True, "data": json.dumps({"status": "pending_approval"})},
        }],
        "context_metadata": {},
    }
    repo.get_message.return_value = message
    repo.get_conversation_history.return_value = [
        {"role": "user", "content": "remove low and high angle"},
        {"role": "assistant", "content": ""},
    ]

    saved_messages = []

    def add_message(**kwargs):
        m = Mock()
        m.id = f"am-{len(saved_messages)}"
        m.model_dump.return_value = {"id": m.id, "role": kwargs["role"], "content": kwargs["content"]}
        saved_messages.append(kwargs)
        return m

    repo.add_message.side_effect = add_message
    return message, saved_messages


class TestApprovalContinuesConversation:
    @pytest.mark.asyncio
    async def test_approve_runs_confirm_and_presents_outcome(self):
        tool = _FakeApprovalTool()
        manager, repo, llm = _make_manager(tool)
        _message, saved = _wire_repo(repo)

        result = await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=True,
        )

        # The confirmed action ran.
        assert tool.confirmed_with is not None
        # A presentation completion was made with tools disabled.
        assert llm.generate_calls and llm.generate_calls[-1]["tools"] == []
        # A new assistant message was persisted with the narrated outcome.
        assert any(m["role"] == "assistant" and m["content"] for m in saved)
        assert result["assistant_message"] is not None
        assert result["assistant_message"]["content"] == "Done — I removed 2 values from camera."

    @pytest.mark.asyncio
    async def test_approve_feeds_confirmed_result_back_to_model(self):
        tool = _FakeApprovalTool()
        manager, repo, llm = _make_manager(tool)
        _wire_repo(repo)

        await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=True,
        )

        messages = llm.generate_calls[-1]["messages"]
        # The reconstructed turn ends with the assistant tool_call and its result.
        assert messages[-2]["role"] == "assistant" and messages[-2]["tool_calls"]
        assert messages[-1]["role"] == "tool"
        assert json.loads(messages[-1]["content"])["deleted_count"] == 2

    @pytest.mark.asyncio
    async def test_deny_acknowledges_without_confirming(self):
        tool = _FakeApprovalTool()
        manager, repo, llm = _make_manager(tool)
        _message, saved = _wire_repo(repo)

        result = await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=False,
        )

        # The action was NOT applied.
        assert tool.confirmed_with is None
        # The model was still asked to acknowledge; the tool message states the rejection.
        tool_msg = llm.generate_calls[-1]["messages"][-1]
        assert tool_msg["role"] == "tool"
        assert "REJECTED" in tool_msg["content"]
        assert result["assistant_message"] is not None


class TestApprovalOutcomeNeverEmpty:
    """An approved (or denied) tool execution must never surface as an
    empty assistant message, even when the presentation LLM produces nothing
    narratable on every retry.
    """

    @pytest.mark.asyncio
    async def test_empty_presentation_falls_back_to_known_tool_narration(self):
        tool = _FakeUpdatePhrasebookTool()
        llm = _AlwaysEmptyLLM()
        manager, repo, _llm = _make_manager(tool, llm=llm)
        _wire_repo(repo, tool_name="update_phrasebook_values")

        result = await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=True,
        )

        # Every retry was exhausted before falling back.
        from src.features.llm.tools.executor import ToolExecutor as _TE
        assert len(llm.generate_calls) == _TE._EMPTY_RESPONSE_MAX_RETRIES
        assert result["assistant_message"] is not None
        assert result["assistant_message"]["content"] == "Done: updated 3 phrasebook values."

    @pytest.mark.asyncio
    async def test_empty_presentation_falls_back_to_generic_narration_for_unknown_tool(self):
        tool = _FakeApprovalTool()
        llm = _AlwaysEmptyLLM()
        manager, repo, _llm = _make_manager(tool, llm=llm)
        _wire_repo(repo)

        result = await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=True,
        )

        content = result["assistant_message"]["content"]
        assert content.strip() != ""
        assert content == "Done: remove stuff completed."

    @pytest.mark.asyncio
    async def test_empty_presentation_on_denial_falls_back_to_denial_narration(self):
        tool = _FakeApprovalTool()
        llm = _AlwaysEmptyLLM()
        manager, repo, _llm = _make_manager(tool, llm=llm)
        _wire_repo(repo)

        result = await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=False,
        )

        content = result["assistant_message"]["content"]
        assert content.strip() != ""
        assert "denied" in content.lower()

    @pytest.mark.asyncio
    async def test_no_empty_content_message_is_ever_persisted(self):
        tool = _FakeApprovalTool()
        llm = _AlwaysEmptyLLM()
        manager, repo, _llm = _make_manager(tool, llm=llm)
        _message, saved = _wire_repo(repo)

        await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=True,
        )

        assert saved, "expected an assistant message to be persisted"
        for m in saved:
            assert (m.get("content") or "").strip() != "", f"empty content persisted: {m}"


class TestPresentationSystemPromptIsToolFree:
    """The presentation turn's system prompt must not carry
    tool-calling instructions — it is a tool-free, narration-only turn.
    """

    @pytest.mark.asyncio
    async def test_presentation_call_excludes_tool_instructions(self):
        tool = _FakeApprovalTool()
        manager, repo, llm = _make_manager(tool)
        _wire_repo(repo)

        await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=True,
        )

        system_message = llm.generate_calls[-1]["custom_system_message"]
        assert "## Tools" not in system_message
        assert "function-calling tools" not in system_message
        assert "tool-free presentation turn" in system_message

    @pytest.mark.asyncio
    async def test_retries_nudge_the_prompt_towards_plain_text(self):
        tool = _FakeApprovalTool()
        llm = _AlwaysEmptyLLM()
        manager, repo, _llm = _make_manager(tool, llm=llm)
        _wire_repo(repo)

        await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=True,
        )

        first_prompt = llm.generate_calls[0]["custom_system_message"]
        last_prompt = llm.generate_calls[-1]["custom_system_message"]
        assert "## Tools" not in first_prompt
        assert first_prompt != last_prompt
        assert "Reminder" in last_prompt


class TestApprovalPreviewPayload:
    @pytest.mark.asyncio
    async def test_remove_tool_fills_structured_preview(self):
        from src.features.llm.tools.builtin.phrasebook_tool import RemovePhrasebookValuesTool
        from src.features.llm.tools.base import ToolContext

        category_repo = MagicMock()
        category_repo.get_by_path.return_value = SimpleNamespace(id="cat-1", path="camera")
        value_repo = MagicMock()
        value_repo.get_by_id.side_effect = [
            SimpleNamespace(id="1", label="low angle", value="low angle", category_id="cat-1"),
            SimpleNamespace(id="2", label="high angle", value="high angle", category_id="cat-1"),
        ]
        ctx = ToolContext(
            user_id="user-1",
            phrasebook_category_repository=category_repo,
            phrasebook_value_repository=value_repo,
        )

        result = await RemovePhrasebookValuesTool().execute(
            ctx, value_ids=["1", "2"], category_path="camera",
        )

        assert result.success
        assert result.preview is not None
        assert result.preview.action == "Remove"
        assert result.preview.target == "from category camera"
        assert result.preview.items == ["low angle", "high angle"]

    @pytest.mark.asyncio
    async def test_preview_serializes_into_tool_end_event(self):
        """The pending tool_end event carries the structured preview for the UI."""
        tool = _FakeApprovalTool()
        registry = ToolRegistry()
        registry.register(tool)
        executor = ToolExecutor(tool_registry=registry, llm_service=Mock())

        events = []

        async def _drain(gen):
            async for e in gen:
                events.append(e)

        await _drain(executor._run_tool_calls_stream(
            [{"id": "c1", "function": {"name": "remove_stuff", "arguments": "{}"}}],
            ToolContext_stub(), [], [], allowed_tools=["remove_stuff"], guard=_ToolCallGuard(),
        ))

        tool_end = next(e for e in events if e["type"] == "tool_end")
        assert tool_end["data"]["pending_approval"] is True
        assert tool_end["data"]["preview"]["action"] == "Remove"
        assert tool_end["data"]["preview"]["items"] == ["a", "b"]


def ToolContext_stub():
    from src.features.llm.tools.base import ToolContext
    return ToolContext(user_id="user-1")
