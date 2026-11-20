from types import SimpleNamespace
from unittest.mock import Mock

from dataclasses import replace
import pytest

from tests.features.chat import test_tool_availability_transparency as availability

from src.features.chat.dto import MessageResponse
from src.features.llm.tools.base import ToolServices
from src.features.chat.runtime import ChatRuntime
from src.features.chat.tool_dispatcher import ToolCallDispatcher
from src.features.llm.tools.base import ToolContext, ToolServices
from src.features.llm.tools.governance import ToolGovernanceRepository, resolve_tool_scope
from tests.fixtures.chat_engine import llm_service_mock


class _CountingGovernance(ToolGovernanceRepository):
    def __init__(self):
        self.snapshot_calls = []
        self.disabled_calls = []

    def get_config_snapshot(self, llm_config_id, tool_names):
        self.snapshot_calls.append((llm_config_id, list(tool_names)))
        return {}

    def get_user_disabled(self, user_id):
        self.disabled_calls.append(user_id)
        return set()


class TestGovernanceResolvedOncePerMessage:
    @pytest.mark.asyncio
    async def test_a_send_reads_governance_once(self):
        manager, _repo, _llm = availability.TestSendMessageToolAvailabilityTransparency()._setup(tools_enabled=False)
        governance = _CountingGovernance()
        manager.tool_governance_repository = governance

        await manager.send_message(session_id="session-123", user_id="user-123", content="Hello")

        assert len(governance.snapshot_calls) == 1
        assert governance.disabled_calls == ["user-123"]

    @pytest.mark.asyncio
    async def test_governance_changes_show_on_the_next_message(self):
        manager, _repo, _llm = availability.TestSendMessageToolAvailabilityTransparency()._setup()
        governance = _CountingGovernance()
        manager.tool_governance_repository = governance

        await manager.send_message(session_id="session-123", user_id="user-123", content="one")
        governance.get_user_disabled = lambda user_id: {"get_data"}
        manager.chat_repository.add_message.side_effect = [
            MessageResponse(id="msg-3", session_id="s", role="user", content="two"),
            MessageResponse(id="msg-4", session_id="s", role="assistant", content="Response"),
        ]
        await manager.send_message(session_id="session-123", user_id="user-123", content="two")

        trace = manager.chat_repository.add_message.call_args_list[-1][1]["metadata"]["behavior_trace"]
        assert trace["tools_withheld"] == {"get_data": "opted_out"}

    def test_one_scope_serves_the_prompt_and_the_withheld_reasons(self):
        governance = _CountingGovernance()
        builder = availability._make_builder(governance)
        session = availability._session()

        scope = builder.resolve_tool_scope(session, None)
        builder.resolve_session_prompt_and_tools(session, scope=scope)
        builder.withheld_tools_for_session(session, scope=scope)

        assert len(governance.snapshot_calls) == 1
        assert len(governance.disabled_calls) == 1

    def test_no_tools_means_no_governance_reads(self):
        governance = _CountingGovernance()

        scope = resolve_tool_scope([], governance, "cfg", "user-1")

        assert scope.allowed_names() == []
        assert governance.snapshot_calls == []
        assert governance.disabled_calls == []


class _Tool:
    def __init__(self, name, available=True):
        self.name = name
        self._available = available

    def is_available(self, form_state):
        return self._available


class TestToolScopeResolution:
    def test_unavailable_and_session_filters_narrow_the_governed_set(self):
        scope = resolve_tool_scope([_Tool("a"), _Tool("b", available=False), _Tool("c")], None, None, None)

        assert scope.unavailable == ("b",)
        assert scope.allowed_names() == ["a", "c"]
        assert scope.allowed_names(disabled_tools=["a"]) == ["c"]
        assert scope.allowed_names(tools_enabled=False) == []

    def test_form_state_reaches_the_availability_predicate(self):
        seen = []

        class _Probe(_Tool):
            def is_available(self, form_state):
                seen.append(form_state)
                return bool(form_state)

        scope = resolve_tool_scope([_Probe("p")], None, None, None, {"doc": 1})

        assert seen == [{"doc": 1}]
        assert scope.allowed_names() == ["p"]


class TestToolServicesHolder:
    def test_for_session_copies_every_service_and_applies_overrides(self):
        services = ToolServices(model_index_manager="models", settings="settings", llm_repository="repo")

        context = ToolContext.for_session(services, "u1", mode_id="m", llm_repository="other", chat_session=False)

        assert (context.user_id, context.mode_id, context.chat_session) == ("u1", "m", False)
        assert context.model_index_manager == "models"
        assert context.settings == "settings"
        assert context.llm_repository == "other"

    def test_dispatcher_builds_the_context_from_the_runtime_services(self):
        runtime = ChatRuntime(
            chat_repository=Mock(), llm_service=llm_service_mock(), response_processor=Mock(), plugin_registry=Mock(),
            chat_mode_registry=Mock(),
            tool_services=ToolServices(llm_memory_repository="first", model_index_manager="models"),
        )
        session = SimpleNamespace(mode="generation", llm_config_id="cfg")
        dispatcher = ToolCallDispatcher(runtime)

        context = dispatcher.tool_context(session, "u1", False, {"k": 1})
        assert context.llm_memory_repository == "first"
        assert context.model_index_manager == "models"
        assert context.session_metadata == {"k": 1}
        assert context.llm_id == "cfg"
        assert context.origin == {"kind": "chat"}

        runtime.tool_services = replace(runtime.tool_services, llm_memory_repository="swapped")
        assert dispatcher.tool_context(session, "u1", False, None).llm_memory_repository == "swapped"
