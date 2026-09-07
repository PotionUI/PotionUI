"""Tests for ManagePromptVariablesTool."""

import json
import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.prompt_variables_tool import ManagePromptVariablesTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_context(session_metadata: dict = None) -> ToolContext:
    return ToolContext(user_id="user-1", session_metadata=session_metadata or {})


def make_form_state(variables: list = None) -> dict:
    return {"variables": variables if variables is not None else []}


def text_var(name: str, value: str) -> dict:
    return {"name": name, "type": "text", "value": value}


def choice_var(name: str, options: list, mode: str = "shuffle", pinned_index=None) -> dict:
    var = {"name": name, "type": "choice", "options": options, "mode": mode}
    if pinned_index is not None:
        var["pinnedIndex"] = pinned_index
    return var


def set_op(name: str, **kwargs) -> dict:
    return {"op": "set", "name": name, **kwargs}


def remove_op(name: str) -> dict:
    return {"op": "remove", "name": name}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_name(self):
        assert ManagePromptVariablesTool().name == "manage_prompt_variables"

    def test_requires_approval(self):
        assert ManagePromptVariablesTool().requires_approval is True

    def test_hint_and_description_nonempty(self):
        tool = ManagePromptVariablesTool()
        assert len(tool.hint) > 0
        assert len(tool.description) > 0

    def test_to_schema_structure(self):
        schema = ManagePromptVariablesTool().to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "manage_prompt_variables"
        assert "operations" in schema["function"]["parameters"]["properties"]


# ---------------------------------------------------------------------------
# execute() – error cases
# ---------------------------------------------------------------------------

class TestExecuteErrors:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_operations_provided(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(ctx, operations=[])
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_returns_error_when_no_form_state(self):
        ctx = make_context()
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("mood", type="text", value="noir")]
        )
        assert result.success is False
        assert "form state" in result.error.lower() or "no form" in result.error.lower()

    @pytest.mark.asyncio
    async def test_rejects_invalid_variable_name(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("has space", type="text", value="x")]
        )
        assert result.success is False
        assert "has space" in result.error

    @pytest.mark.asyncio
    async def test_rejects_name_starting_with_digit(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("1mood", type="text", value="x")]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_rejects_removing_unknown_variable(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(ctx, operations=[remove_op("ghost")])
        assert result.success is False
        assert "ghost" in result.error.lower()

    @pytest.mark.asyncio
    async def test_rejects_choice_with_no_options(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("mood", type="choice", options=[])]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_rejects_choice_with_too_many_options(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx,
            operations=[set_op("mood", type="choice", options=[f"o{i}" for i in range(13)])],
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_rejects_unknown_mode(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("mood", type="choice", options=["a", "b"], mode="random")]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_rejects_out_of_range_pinned_index(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx,
            operations=[
                set_op("mood", type="choice", options=["a", "b"], mode="pin", pinned_index=9)
            ],
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_rejects_unknown_op(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[{"op": "rename", "name": "mood"}]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_rejects_batch_exceeding_variable_cap(self):
        existing = [text_var(f"v{i}", "x") for i in range(24)]
        ctx = make_context(session_metadata={"form_state": make_form_state(existing)})
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("one_more", type="text", value="y")]
        )
        assert result.success is False
        assert "24" in result.error


# ---------------------------------------------------------------------------
# execute() – happy path
# ---------------------------------------------------------------------------

class TestExecuteSuccess:
    @pytest.mark.asyncio
    async def test_new_text_variable_preview(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("mood", type="text", value="noir")]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["status"] == "pending_approval"
        assert payload["change_count"] == 1
        row = payload["proposed_changes"][0]
        assert row["field_name"] == "${mood}"
        assert row["old_value"] == "not set"
        assert "noir" in row["new_value"]

    @pytest.mark.asyncio
    async def test_update_existing_text_variable_shows_old_value(self):
        existing = [text_var("mood", "sunlit")]
        ctx = make_context(session_metadata={"form_state": make_form_state(existing)})
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("mood", type="text", value="noir")]
        )
        payload = json.loads(result.data)
        row = payload["proposed_changes"][0]
        assert "sunlit" in row["old_value"]
        assert "noir" in row["new_value"]

    @pytest.mark.asyncio
    async def test_update_omits_type_falls_back_to_existing_type(self):
        existing = [choice_var("mood", ["noir", "sunlit"])]
        ctx = make_context(session_metadata={"form_state": make_form_state(existing)})
        result = await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("mood", options=["dawn", "dusk"])]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert "dawn" in payload["proposed_changes"][0]["new_value"]

    @pytest.mark.asyncio
    async def test_new_choice_variable_preview(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx,
            operations=[set_op("time", type="choice", options=["dawn", "dusk"], mode="per-image")],
        )
        payload = json.loads(result.data)
        row = payload["proposed_changes"][0]
        assert row["field_name"] == "${time}"
        assert "dawn" in row["new_value"] and "dusk" in row["new_value"]

    @pytest.mark.asyncio
    async def test_remove_existing_variable_preview(self):
        existing = [text_var("mood", "noir")]
        ctx = make_context(session_metadata={"form_state": make_form_state(existing)})
        result = await ManagePromptVariablesTool().execute(ctx, operations=[remove_op("mood")])
        assert result.success is True
        payload = json.loads(result.data)
        row = payload["proposed_changes"][0]
        assert row["field_name"] == "${mood}"
        assert row["new_value"] == "(removed)"

    @pytest.mark.asyncio
    async def test_reason_included_when_provided(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx,
            operations=[set_op("mood", type="text", value="noir", reason="Better fits the shot")],
        )
        payload = json.loads(result.data)
        assert payload["proposed_changes"][0]["reason"] == "Better fits the shot"

    @pytest.mark.asyncio
    async def test_partial_success_with_one_invalid_operation(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx,
            operations=[
                set_op("mood", type="text", value="noir"),
                remove_op("ghost"),
            ],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["change_count"] == 1
        assert "warnings" in payload
        assert any("ghost" in w.lower() for w in payload["warnings"])

    @pytest.mark.asyncio
    async def test_execute_does_not_mutate_form_state(self):
        existing = [text_var("mood", "noir")]
        form_state = make_form_state(existing)
        ctx = make_context(session_metadata={"form_state": form_state})
        await ManagePromptVariablesTool().execute(
            ctx, operations=[set_op("mood", type="text", value="sunlit")]
        )
        assert form_state["variables"][0]["value"] == "noir"

    @pytest.mark.asyncio
    async def test_sequential_ops_on_same_name_use_intermediate_state(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute(
            ctx,
            operations=[
                set_op("mood", type="text", value="noir"),
                set_op("mood", type="text", value="sunlit"),
            ],
        )
        payload = json.loads(result.data)
        assert payload["change_count"] == 2
        assert "not set" in payload["proposed_changes"][0]["old_value"]
        assert "noir" in payload["proposed_changes"][1]["old_value"]
        assert "sunlit" in payload["proposed_changes"][1]["new_value"]


# ---------------------------------------------------------------------------
# execute_confirmed()
# ---------------------------------------------------------------------------

class TestExecuteConfirmed:
    @pytest.mark.asyncio
    async def test_returns_apply_variable_changes_action(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute_confirmed(
            ctx, operations=[set_op("mood", type="text", value="noir")]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["action"] == "apply_variable_changes"

    @pytest.mark.asyncio
    async def test_set_text_operation_shape(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute_confirmed(
            ctx, operations=[set_op("mood", type="text", value="noir")]
        )
        payload = json.loads(result.data)
        op = payload["operations"][0]
        assert op == {"op": "set", "name": "mood", "type": "text", "value": "noir"}

    @pytest.mark.asyncio
    async def test_set_choice_operation_shape(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute_confirmed(
            ctx,
            operations=[
                set_op("time", type="choice", options=["dawn", "dusk"], mode="pin", pinned_index=1)
            ],
        )
        payload = json.loads(result.data)
        op = payload["operations"][0]
        assert op == {
            "op": "set",
            "name": "time",
            "type": "choice",
            "options": ["dawn", "dusk"],
            "mode": "pin",
            "pinned_index": 1,
        }

    @pytest.mark.asyncio
    async def test_remove_operation_shape(self):
        existing = [text_var("mood", "noir")]
        ctx = make_context(session_metadata={"form_state": make_form_state(existing)})
        result = await ManagePromptVariablesTool().execute_confirmed(
            ctx, operations=[remove_op("mood")]
        )
        payload = json.loads(result.data)
        assert payload["operations"][0] == {"op": "remove", "name": "mood"}

    @pytest.mark.asyncio
    async def test_reruns_validation_rejects_unknown_removal_even_if_replayed(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await ManagePromptVariablesTool().execute_confirmed(
            ctx, operations=[remove_op("ghost")]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_works_without_form_state(self):
        ctx = make_context()
        result = await ManagePromptVariablesTool().execute_confirmed(
            ctx, operations=[set_op("mood", type="text", value="noir")]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["operations"][0]["name"] == "mood"

    @pytest.mark.asyncio
    async def test_cap_breach_rejected_on_confirm_too(self):
        existing = [text_var(f"v{i}", "x") for i in range(24)]
        ctx = make_context(session_metadata={"form_state": make_form_state(existing)})
        result = await ManagePromptVariablesTool().execute_confirmed(
            ctx, operations=[set_op("one_more", type="text", value="y")]
        )
        assert result.success is False
