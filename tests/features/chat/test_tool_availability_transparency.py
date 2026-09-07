"""Tools a mode declares but a session can't actually call this turn (toggled
off, disabled for the mode, governed off/opted out, or unavailable) must not
be left for the model to discover on its own — its prompt is written for the
tool-driven case, so with no tools offered it narrates calling one anyway
(reported: "Firing all 5 caption updates now..." with no tool step in the
trace and nothing in the approval dock).

Covers ChatContextBuilder.withheld_tools_for_session (reason classification),
ChatContextBuilder.inject_tool_availability_block (the system warning), and
their wiring into ConversationRunner.send_message's persisted behavior trace.
"""

import io
import sys
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tests.conftest import TestDatabase

from src.features.chat.context_builder import ChatContextBuilder
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.chat.runtime import ChatRuntime
from src.features.llm.tools.governance import ToolGovernanceRepository


@pytest.fixture
def governance_db():
    test_database = TestDatabase()
    with patch("src.platform.database.database.db", test_database), \
         patch("src.platform.database.migration_runner.db", test_database):
        from src.platform.database.migration_runner import MigrationRunner

        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            MigrationRunner().run_migrations()
        finally:
            sys.stdout = old_stdout

        yield test_database
    test_database.close()


def _mode_registry() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


def _session(user_id="user-1", llm_config_id="cfg-a", mode="generation", metadata=None):
    session = Mock()
    session.mode = mode
    session.metadata = metadata
    session.user_id = user_id
    session.llm_config_id = llm_config_id
    return session


def _make_builder(governance_repo=None):
    """Real ToolRegistry with three real "generation" tools: get_form_state
    and get_active_models are always available; get_current_segments is
    unavailable when the Video Director is active — the one real is_available
    conditional in the builtin set, used to exercise the "unavailable" reason
    without a mock."""
    from src.features.llm.tools.registry import ToolRegistry
    from src.features.llm.tools.builtin.form_context_tool import GetFormStateTool, GetCurrentSegmentsTool
    from src.features.llm.tools.builtin.active_models_tool import GetActiveModelsTool

    tool_registry = ToolRegistry()
    tool_registry.register(GetFormStateTool())
    tool_registry.register(GetActiveModelsTool())
    tool_registry.register(GetCurrentSegmentsTool())

    manager = Mock()
    manager.chat_mode_registry = _mode_registry()
    tool_executor = Mock()
    tool_executor.tool_registry = tool_registry
    manager.tool_executor = tool_executor
    manager.tool_governance_repository = governance_repo
    return ChatContextBuilder(manager)


class TestWithheldToolsForSession:
    def test_all_allowed_is_empty(self, governance_db):
        builder = _make_builder(ToolGovernanceRepository())
        assert builder.withheld_tools_for_session(_session()) == {}

    def test_no_mode_tools_is_empty(self):
        manager = Mock()
        manager.chat_mode_registry = _mode_registry()
        manager.tool_executor = None
        builder = ChatContextBuilder(manager)
        assert builder.withheld_tools_for_session(_session()) == {}

    def test_off_by_toggle_when_tools_enabled_false(self, governance_db):
        builder = _make_builder(ToolGovernanceRepository())
        withheld = builder.withheld_tools_for_session(_session(metadata={"tools_enabled": False}))
        assert withheld == {
            "get_form_state": "off_by_toggle",
            "get_active_models": "off_by_toggle",
            "get_current_segments": "off_by_toggle",
        }

    def test_disabled_in_mode_for_the_specific_excluded_tool(self, governance_db):
        builder = _make_builder(ToolGovernanceRepository())
        session = _session(metadata={"disabled_tools": ["get_active_models"]})
        withheld = builder.withheld_tools_for_session(session)
        assert withheld == {"get_active_models": "disabled_in_mode"}

    def test_new_mode_tool_not_in_disabled_tools_is_offered(self, governance_db):
        """A tool registered into the mode after the session's disabled_tools
        was chosen (a restart with a new builtin, a plugin enabled) is offered
        by default: the subtractive metadata only names what was explicitly
        unticked, so it never goes stale against tools added later."""
        from src.features.llm.tools.registry import ToolRegistry
        from src.features.llm.tools.builtin.form_context_tool import GetFormStateTool
        from src.features.llm.tools.builtin.active_models_tool import GetActiveModelsTool

        tool_registry = ToolRegistry()
        tool_registry.register(GetFormStateTool())

        manager = Mock()
        manager.chat_mode_registry = _mode_registry()
        tool_executor = Mock()
        tool_executor.tool_registry = tool_registry
        manager.tool_executor = tool_executor
        manager.tool_governance_repository = ToolGovernanceRepository()
        builder = ChatContextBuilder(manager)

        session = _session(metadata={"disabled_tools": ["get_form_state"]})
        assert builder.withheld_tools_for_session(session) == {"get_form_state": "disabled_in_mode"}

        # A new tool joins the mode later; the session never unticked it.
        tool_registry.register(GetActiveModelsTool())
        assert builder.withheld_tools_for_session(session) == {"get_form_state": "disabled_in_mode"}

    def test_unavailable_when_is_available_false(self, governance_db):
        builder = _make_builder(ToolGovernanceRepository())
        form_state = {"video_director": {"active": True}}
        withheld = builder.withheld_tools_for_session(_session(), form_state=form_state)
        assert withheld == {"get_current_segments": "unavailable"}

    def test_disabled_by_admin(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "get_active_models", enabled=False)
        builder = _make_builder(repo)
        withheld = builder.withheld_tools_for_session(_session())
        assert withheld == {"get_active_models": "disabled_by_admin"}

    def test_opted_out(self, governance_db):
        repo = ToolGovernanceRepository()
        repo.set_user_disabled("user-1", "get_active_models", True)
        builder = _make_builder(repo)
        withheld = builder.withheld_tools_for_session(_session(user_id="user-1"))
        assert withheld == {"get_active_models": "opted_out"}

    def test_locked_admin_config_overrides_user_opt_out_to_allowed(self, governance_db):
        """A locked config ignores the user's opt-out, so the tool stays allowed
        and is absent from the withheld map — not misreported as opted_out."""
        repo = ToolGovernanceRepository()
        repo.upsert_config("cfg-a", "get_active_models", enabled=True, locked=True)
        repo.set_user_disabled("user-1", "get_active_models", True)
        builder = _make_builder(repo)
        withheld = builder.withheld_tools_for_session(_session(user_id="user-1"))
        assert "get_active_models" not in withheld


class TestInjectToolAvailabilityBlock:
    def test_noop_when_nothing_withheld(self):
        history = [{"role": "user", "content": "hi"}]
        ChatContextBuilder.inject_tool_availability_block(history, ["get_form_state"], {})
        assert history == [{"role": "user", "content": "hi"}]

    def test_all_withheld_warns_no_tool_calls_at_all(self):
        history = [{"role": "user", "content": "hi"}]
        ChatContextBuilder.inject_tool_availability_block(
            history, [], {"propose_caption_update": "off_by_toggle"}
        )
        assert len(history) == 2
        block = history[0]
        assert block["role"] == "system"
        assert "Tools are unavailable in this conversation" in block["content"]
        assert "You cannot call any tool" in block["content"]
        assert "turn on Tools in the chat header" in block["content"]
        assert "Never announce an action you cannot perform." in block["content"]

    def test_partial_withheld_names_only_the_missing_tools(self):
        history = [{"role": "user", "content": "hi"}]
        ChatContextBuilder.inject_tool_availability_block(
            history, ["get_form_state"], {"get_active_models": "disabled_in_mode"}
        )
        assert len(history) == 2
        block = history[0]["content"]
        assert "get_active_models" in block
        assert "disabled for this mode" in block
        assert "enable it in this mode's Tools panel" in block
        # A partially-withheld block must not claim no tools can be called at all.
        assert "You cannot call any tool" not in block

    def test_inserted_immediately_before_last_message(self):
        history = [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "latest"},
        ]
        ChatContextBuilder.inject_tool_availability_block(history, [], {"t": "unavailable"})
        assert history[3]["content"] == "latest"
        assert history[2]["role"] == "system"


def _tool_session(tools_enabled=True, disabled_tools=None):
    session = Mock()
    session.user_id = "user-123"
    session.status = "active"
    session.llm_config_id = "llm-123"
    session.mode = "generation"
    metadata = {}
    if not tools_enabled:
        metadata["tools_enabled"] = False
    if disabled_tools:
        metadata["disabled_tools"] = disabled_tools
    session.metadata = metadata or None
    return session


class TestSendMessageToolAvailabilityTransparency:
    """Wiring into ConversationRunner.send_message (buffered path) — both send
    paths share the same ChatContextBuilder calls, so this covers the shared
    logic; the streaming path only adds an SSE status event around it."""

    def _setup(self, tools_enabled=True, disabled_tools=None):
        mock_repo = Mock()
        mock_llm = AsyncMock()
        mock_processor = Mock()
        mock_plugins = Mock()
        mock_context = Mock()
        mock_context.data = {}
        mock_plugins.execute_hook.return_value = (mock_context, [])
        mock_processor.process.side_effect = lambda content, mode=None: (content, {"raw": content})

        tool_a = Mock()
        tool_a.name = "get_data"
        tool_a.hint = ""
        tool_a.is_available.return_value = True
        tool_b = Mock()
        tool_b.name = "write_data"
        tool_b.hint = ""
        tool_b.is_available.return_value = True

        mock_tool_executor = Mock()
        mock_tool_executor.tool_registry.get_for_mode.return_value = [tool_a, tool_b]
        mock_tool_executor.tool_registry.get_tool_hints_text.return_value = ""

        response = Mock()
        response.content = "AI response"
        response.model = "test-model"
        response.tokens_used = 15
        response.prompt_tokens = 10
        response.completion_tokens = 5
        response.rescues = None
        response.tool_failures = None
        response.thinking_mode = None
        mock_llm.generate_with_history.return_value = response
        mock_tool_executor.execute_with_tools = AsyncMock(return_value=(response, []))

        manager = ChatRuntime(
            chat_repository=mock_repo,
            llm_service=mock_llm,
            response_processor=mock_processor,
            plugin_registry=mock_plugins,
            chat_mode_registry=_mode_registry(),
            tool_executor=mock_tool_executor,
        )

        session = _tool_session(tools_enabled=tools_enabled, disabled_tools=disabled_tools)
        mock_repo.get_session.return_value = session
        mock_repo.get_conversation_history.return_value = []

        from src.features.chat.dto import MessageResponse
        user_msg = MessageResponse(id="msg-1", session_id="s", role="user", content="Hello")
        assistant_msg = MessageResponse(id="msg-2", session_id="s", role="assistant", content="Response")
        mock_repo.add_message.side_effect = [user_msg, assistant_msg]

        return manager, mock_repo, mock_llm

    @pytest.mark.asyncio
    async def test_all_tools_off_injects_block_and_records_trace(self):
        manager, mock_repo, mock_llm = self._setup(tools_enabled=False)

        await manager.send_message(session_id="session-123", user_id="user-123", content="Hello")

        sent_history = mock_llm.generate_with_history.call_args.kwargs["messages"]
        system_blocks = [m["content"] for m in sent_history if m["role"] == "system"]
        assert any("Tools are unavailable in this conversation" in b for b in system_blocks)

        second_call = mock_repo.add_message.call_args_list[1][1]
        trace = second_call["metadata"]["behavior_trace"]
        assert trace["tools_offered"] == []
        assert trace["tools_withheld"] == {"get_data": "off_by_toggle", "write_data": "off_by_toggle"}
        assert "tools" in [s["step"] for s in trace["steps"]]

    @pytest.mark.asyncio
    async def test_all_tools_allowed_injects_nothing(self):
        manager, mock_repo, mock_llm = self._setup()

        await manager.send_message(session_id="session-123", user_id="user-123", content="Hello")

        kwargs = manager.tool_executor.execute_with_tools.call_args.kwargs
        sent_history = kwargs["messages"]
        system_blocks = [m["content"] for m in sent_history if m["role"] == "system"]
        assert not any("Tools are unavailable" in b or "unavailable this turn" in b for b in system_blocks)

        second_call = mock_repo.add_message.call_args_list[1][1]
        trace = second_call["metadata"]["behavior_trace"]
        assert trace["tools_offered"] == ["get_data", "write_data"]
        assert trace["tools_withheld"] == {}
        assert "tools" not in [s["step"] for s in trace["steps"]]
