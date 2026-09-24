import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.features.chat.runtime import ChatRuntime
from src.features.chat.modes import ChatModeRegistry, build_generation_mode
from src.features.llm.tools.builtin.prompt_variables_tool import ManagePromptVariablesTool
from src.features.llm.tools.builtin.run_generation_tool import RunGenerationTool
from src.features.llm.tools.executor import ToolExecutor
from src.features.llm.tools.registry import ToolRegistry
from src.features.prompt.expander import expand_prompts


def _mode_registry() -> ChatModeRegistry:
    registry = ChatModeRegistry()
    registry.register(build_generation_mode())
    return registry


class _ChainToRunGenerationLLM:
    def __init__(self):
        self.repository = Mock()
        self.repository.get_configuration.return_value = SimpleNamespace(provider_options={})
        self.generate_calls = []

    async def generate_with_tools(self, **kwargs):
        self.generate_calls.append(kwargs)
        if len(self.generate_calls) == 1:
            return SimpleNamespace(
                content="",
                tool_calls=[{
                    "id": "chain_call_0",
                    "function": {"name": "run_generation", "arguments": "{}"},
                }],
                model="m", tokens_used=5, prompt_tokens=3, completion_tokens=2,
            )
        return SimpleNamespace(
            content="Sure, running that now.",
            tool_calls=[], model="m", tokens_used=7, prompt_tokens=4, completion_tokens=3,
        )


def _make_manager(generation_orchestrator):
    repo = Mock()
    processor = Mock()
    processor.process.side_effect = lambda content, mode=None: ((content or "").strip(), None)
    plugins = Mock()
    plugins.execute_hook.return_value = (Mock(data={}), [])

    registry = ToolRegistry()
    registry.register(ManagePromptVariablesTool())
    registry.register(RunGenerationTool())
    llm = _ChainToRunGenerationLLM()
    executor = ToolExecutor(tool_registry=registry, llm_service=llm)

    manager = ChatRuntime(
        chat_repository=repo,
        llm_service=llm,
        response_processor=processor,
        plugin_registry=plugins,
        chat_mode_registry=_mode_registry(),
        tool_executor=executor,
        generation_orchestrator=generation_orchestrator,
    )
    return manager, repo, llm


def _wire_repo(repo):
    session = Mock()
    session.id = "session-1"
    session.user_id = "user-1"
    session.status = "active"
    session.llm_config_id = "llm-1"
    session.mode = "generation"
    session.metadata = {}
    repo.get_session.return_value = session

    first_message = Mock()
    first_message.session_id = "session-1"
    first_message.metadata = {
        "tool_executions": [{
            "tool_name": "manage_prompt_variables",
            "arguments": {
                "operations": [{
                    "op": "set", "name": "mood", "type": "choice",
                    "options": ["noir", "sunlit"], "mode": "pin", "pinned_index": 1,
                }],
            },
            "pending_approval": True,
            "result": {"success": True, "data": json.dumps({"status": "pending_approval"})},
        }],
        "context_metadata": {
            "form_state": {
                "preset": "preset-sdxl",
                "mode": "txt2img",
                "form_data": {"prompt": "${mood} lighting on the subject"},
                "variables": [],
            },
        },
    }

    repo.get_conversation_history.return_value = [
        {"role": "user", "content": "make mood pinned to sunlit, then generate"},
        {"role": "assistant", "content": ""},
    ]

    messages_by_id = {"msg-1": first_message}
    saved_messages = []

    def add_message(**kwargs):
        m = Mock()
        m.id = f"am-{len(saved_messages)}"
        m.model_dump.return_value = {"id": m.id, "role": kwargs["role"], "content": kwargs["content"]}
        saved_messages.append(kwargs)
        record = Mock()
        record.session_id = "session-1"
        record.metadata = kwargs.get("metadata") or {}
        messages_by_id[m.id] = record
        return m

    repo.add_message.side_effect = add_message
    repo.get_message.side_effect = lambda message_id: messages_by_id.get(message_id)
    repo.update_message_metadata.side_effect = lambda message_id, metadata: (
        setattr(messages_by_id[message_id], "metadata", metadata) if message_id in messages_by_id else None
    )
    return saved_messages


class TestFormStateSyncAcrossChainedApprovals:
    @pytest.mark.asyncio
    async def test_run_generation_pending_after_variables_approval_carries_updated_snapshot(self):
        orchestrator = AsyncMock()
        orchestrator.start_generation.return_value = {"generation_id": "gen-1", "status": {"status": "pending"}}
        manager, repo, llm = _make_manager(orchestrator)
        saved = _wire_repo(repo)

        await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=True,
        )

        persisted = saved[-1]
        run_gen_execution = persisted["metadata"]["tool_executions"][0]
        assert run_gen_execution["tool_name"] == "run_generation"
        assert run_gen_execution["pending_approval"] is True

        synced_variables = persisted["metadata"]["context_metadata"]["form_state"]["variables"]
        assert synced_variables == [{
            "name": "mood", "type": "choice", "options": ["noir", "sunlit"],
            "mode": "pin", "pinnedIndex": 1,
        }]

    @pytest.mark.asyncio
    async def test_approving_the_chained_run_generation_expands_the_new_variable(self):
        orchestrator = AsyncMock()
        orchestrator.start_generation.return_value = {"generation_id": "gen-1", "status": {"status": "pending"}}
        manager, repo, llm = _make_manager(orchestrator)
        _saved = _wire_repo(repo)

        await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="msg-1",
            tool_index=0, approved=True,
        )

        await manager.approve_tool_execution(
            session_id="session-1", user_id="user-1", message_id="am-0",
            tool_index=0, approved=True,
        )

        request = orchestrator.start_generation.call_args[1]["request"]
        assert request.variables == {"mood": "sunlit"}
        expanded = expand_prompts(
            request.prompts[0].positive, request.prompts[0].negative,
            count=1, base_seed=0, variables=request.variables,
        )
        assert expanded[0].positive == "sunlit lighting on the subject"
        assert "${mood}" not in expanded[0].positive
