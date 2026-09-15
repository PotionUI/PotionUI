"""A wire client builds exactly one system message, from the mode/config
prompt, at index 0 -- never from `conversation_history`/`working_messages`.
Every per-turn injection (context blocks, tool-loop nudges) instead folds
into the current user turn via `context_budget.attach_context_block`, since a
mid-list system message is rejected by some chat templates and invalidates
the KV prefix cache on every turn.

Covers both producers: `ChatContextBuilder`'s per-send context blocks and
`ToolWorkflow`'s per-round nudges.
"""

from unittest.mock import Mock

from src.features.chat.context_builder import ChatContextBuilder
from src.features.chat.modes import ChatMode
from src.features.chat.reply_contract import REPLY_CONTRACT_REMINDER
from src.features.llm.tools.workflow import ToolWorkflow


def _no_system_messages(messages):
    return not any(m.get("role") == "system" for m in messages)


class TestChatContextBuilderNeverInsertsSystemMessages:
    def test_every_static_injector_folds_into_the_last_user_message(self):
        history = [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "current question"},
        ]

        ChatContextBuilder.inject_resource_block(history, [])  # no-op, nothing resolved
        ChatContextBuilder.inject_prompt_state_block(
            history,
            {"segments": [{"index": 0, "id": "a", "type": "content", "enabled": True, "content": "x"}]},
        )
        ChatContextBuilder.inject_reply_contract_reminder_block(
            history, ChatMode(id="test", name="Test", structured_reply=True)
        )
        ChatContextBuilder.inject_tool_availability_block(history, [], {"get_active_models": "off_by_toggle"})

        assert _no_system_messages(history)
        assert len(history) == 3
        assert history[0] == {"role": "user", "content": "earlier"}
        assert history[1] == {"role": "assistant", "content": "reply"}

        folded = history[2]
        assert folded["role"] == "user"
        assert folded["content"].startswith("current question\n\n<context>\n")
        assert "PROMPT STATE" in folded["content"]
        assert REPLY_CONTRACT_REMINDER in folded["content"]
        assert "Tools are unavailable in this conversation" in folded["content"]

    def test_contributor_and_memory_blocks_fold_in_too(self):
        """The two non-static injectors (they read collaborators off the
        manager) follow the same contract."""
        manager = Mock()
        manager.chat_mode_registry.get.return_value = ChatMode(
            id="custom", name="Custom", context_contributor=lambda cm, s, u: "CONTRIBUTOR BLOCK",
        )
        manager.llm_memory_repository = None  # inject_memory_block no-ops without one
        builder = ChatContextBuilder(manager)

        history = [{"role": "user", "content": "hello"}]
        import asyncio
        asyncio.run(builder.inject_contributor_block(history, Mock(mode="custom"), {}, "user-1"))

        assert _no_system_messages(history)
        assert len(history) == 1
        assert history[0]["content"] == "hello\n\n<context>\nCONTRIBUTOR BLOCK\n</context>"


class TestToolWorkflowNeverInsertsSystemMessages:
    def _workflow(self, **kwargs):
        return ToolWorkflow(
            executor=Mock(),
            messages=[{"role": "user", "content": "go"}],
            tool_context=Mock(),
            allowed_tools=None,
            max_iterations=5,
            **kwargs,
        )

    def test_iteration_nudge_folds_into_the_user_turn(self):
        workflow = self._workflow(iteration_nudge="call a tool now")
        workflow.working_messages.append({
            "role": "assistant", "content": "", "tool_calls": [{"function": {"name": "f"}}],
        })
        workflow.working_messages.append({"role": "tool", "content": "result", "tool_call_id": "1"})
        workflow._any_tool_round_completed = True

        request = workflow._next_request()

        assert _no_system_messages(request.messages)
        question = next(m for m in request.messages if m["role"] == "user")
        assert question["content"] == "go\n\n<context>\ncall a tool now\n</context>"
        assert workflow.working_messages[0]["content"] == "go"

    def test_final_wrap_up_message_folds_into_the_user_turn(self):
        workflow = self._workflow(wrap_up_on_limit=True)

        request = workflow._final_request()

        assert _no_system_messages(request.messages)
        question = next(m for m in request.messages if m["role"] == "user")
        assert question["content"] == f"go\n\n<context>\n{ToolWorkflow.TOOL_BUDGET_EXHAUSTED_MESSAGE}\n</context>"

    def test_steer_nudge_folds_into_the_user_turn(self):
        workflow = self._workflow()

        workflow._steer("cleaned attempt", "try again")

        assert _no_system_messages(workflow.working_messages)
        question = next(m for m in workflow.working_messages if m["role"] == "user")
        assert question["content"] == "go\n\n<context>\ntry again\n</context>"
