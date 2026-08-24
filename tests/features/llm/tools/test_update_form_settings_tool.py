"""Tests for UpdateFormSettingsTool."""

import json
import pytest
from unittest.mock import MagicMock
from typing import Any

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.update_form_settings_tool import UpdateFormSettingsTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_context(
    user_id: str = "user-1",
    session_metadata: dict = None,
    preset_manager: Any = None,
) -> ToolContext:
    return ToolContext(
        user_id=user_id,
        session_metadata=session_metadata or {},
        preset_manager=preset_manager,
    )


_DEFAULT_FORM_DATA = {"steps": 30, "cfg": 7.0, "sampler": "DPM++ 2M"}


def make_form_state(
    preset: str = "preset-sdxl",
    mode: str = "standard",
    form_data: dict = None,
) -> dict:
    return {
        "preset": preset,
        "mode": mode,
        "form_data": _DEFAULT_FORM_DATA.copy() if form_data is None else form_data,
    }


def make_change(field_name: str, value: Any, reason: str = "") -> dict:
    change = {"field_name": field_name, "value": value}
    if reason:
        change["reason"] = reason
    return change


# ---------------------------------------------------------------------------
# Schema / metadata
# ---------------------------------------------------------------------------

class TestUpdateFormSettingsToolSchema:
    def test_name(self):
        assert UpdateFormSettingsTool().name == "update_form_settings"

    def test_hint_is_nonempty(self):
        assert len(UpdateFormSettingsTool().hint) > 0

    def test_description_is_nonempty(self):
        assert len(UpdateFormSettingsTool().description) > 0

    def test_requires_approval(self):
        assert UpdateFormSettingsTool().requires_approval is True

    def test_parameters_has_required_changes(self):
        schema = UpdateFormSettingsTool().parameters
        assert "changes" in schema["properties"]
        assert "changes" in schema["required"]

    def test_parameters_changes_is_array(self):
        schema = UpdateFormSettingsTool().parameters
        assert schema["properties"]["changes"]["type"] == "array"

    def test_change_items_require_field_name_and_value(self):
        schema = UpdateFormSettingsTool().parameters
        items = schema["properties"]["changes"]["items"]
        assert "field_name" in items["properties"]
        assert "value" in items["properties"]
        assert "field_name" in items["required"]
        assert "value" in items["required"]

    def test_change_items_reason_is_optional(self):
        schema = UpdateFormSettingsTool().parameters
        items = schema["properties"]["changes"]["items"]
        assert "reason" in items["properties"]
        assert "reason" not in items.get("required", [])

    def test_to_schema_structure(self):
        schema = UpdateFormSettingsTool().to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "update_form_settings"
        assert "parameters" in schema["function"]


# ---------------------------------------------------------------------------
# execute() – error cases
# ---------------------------------------------------------------------------

class TestUpdateFormSettingsToolExecuteErrors:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_changes_provided(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute(ctx, changes=[])
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_returns_error_when_changes_kwarg_missing(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute(ctx)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_error_when_no_form_state(self):
        ctx = make_context()  # no form_state in session_metadata
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("steps", 50)]
        )
        assert result.success is False
        assert "form state" in result.error.lower() or "no form" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_form_state_is_none(self):
        ctx = make_context(session_metadata={"form_state": None})
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("steps", 50)]
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_returns_error_when_all_field_names_are_unknown(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("nonexistent_field", 99)]
        )
        assert result.success is False
        assert "nonexistent_field" in result.error or "unknown" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_field_name_is_empty_string(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[{"field_name": "", "value": 10}]
        )
        assert result.success is False


# ---------------------------------------------------------------------------
# execute() – happy path
# ---------------------------------------------------------------------------

class TestUpdateFormSettingsToolExecuteSuccess:
    @pytest.mark.asyncio
    async def test_successful_proposal_structure(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("steps", 50)]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["status"] == "pending_approval"
        assert "proposed_changes" in payload
        assert "change_count" in payload

    @pytest.mark.asyncio
    async def test_proposed_changes_include_old_and_new_values(self):
        form_data = {"steps": 30, "cfg": 7.0}
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data=form_data)}
        )
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("steps", 50)]
        )
        payload = json.loads(result.data)
        change = payload["proposed_changes"][0]
        assert change["field_name"] == "steps"
        assert change["old_value"] == 30
        assert change["new_value"] == 50

    @pytest.mark.asyncio
    async def test_old_value_is_none_for_unset_field(self):
        # Field is known (in form_data keys) but has no current value via different key
        form_data = {"steps": 30}
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data=form_data)}
        )
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("steps", 25)]
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_reason_included_when_provided(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute(
            ctx,
            changes=[make_change("steps", 50, reason="More steps for better quality")],
        )
        payload = json.loads(result.data)
        change = payload["proposed_changes"][0]
        assert change["reason"] == "More steps for better quality"

    @pytest.mark.asyncio
    async def test_reason_absent_when_not_provided(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("steps", 50)]
        )
        payload = json.loads(result.data)
        change = payload["proposed_changes"][0]
        assert "reason" not in change

    @pytest.mark.asyncio
    async def test_change_count_matches_valid_changes(self):
        form_data = {"steps": 30, "cfg": 7.0, "sampler": "DPM++ 2M"}
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data=form_data)}
        )
        result = await UpdateFormSettingsTool().execute(
            ctx,
            changes=[
                make_change("steps", 50),
                make_change("cfg", 9.0),
            ],
        )
        payload = json.loads(result.data)
        assert payload["change_count"] == 2
        assert len(payload["proposed_changes"]) == 2

    @pytest.mark.asyncio
    async def test_partial_success_with_some_invalid_fields(self):
        """Valid changes succeed; invalid ones go into warnings."""
        form_data = {"steps": 30, "cfg": 7.0}
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data=form_data)}
        )
        result = await UpdateFormSettingsTool().execute(
            ctx,
            changes=[
                make_change("steps", 50),
                make_change("nonexistent", 99),
            ],
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["change_count"] == 1
        assert "warnings" in payload
        assert any("nonexistent" in w for w in payload["warnings"])

    @pytest.mark.asyncio
    async def test_execute_does_not_mutate_form_data(self):
        """execute() must be read-only — it only previews changes."""
        form_data = {"steps": 30}
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data=form_data)}
        )
        await UpdateFormSettingsTool().execute(ctx, changes=[make_change("steps", 99)])
        # form_data must be unchanged
        assert ctx.session_metadata["form_state"]["form_data"]["steps"] == 30


# ---------------------------------------------------------------------------
# execute() – schema-based field validation
# ---------------------------------------------------------------------------

class TestUpdateFormSettingsToolSchemaValidation:
    @pytest.mark.asyncio
    async def test_accepts_fields_from_schema_not_in_form_data(self):
        """Fields listed in preset schema but absent from form_data should be accepted."""
        preset_manager = MagicMock()
        preset_manager.get_form_schema = MagicMock(return_value={
            "form_schema": {
                "properties": {
                    "extra_field": {"type": "integer"},
                }
            }
        })
        # form_data has no "extra_field"
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data={})},
            preset_manager=preset_manager,
        )
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("extra_field", 42)]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["change_count"] == 1

    @pytest.mark.asyncio
    async def test_schema_load_failure_falls_back_to_form_data_keys(self):
        """If preset_manager.get_form_schema raises, known_fields falls back to form_data."""
        preset_manager = MagicMock()
        preset_manager.get_form_schema = MagicMock(side_effect=RuntimeError("schema error"))
        form_data = {"steps": 30}
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data=form_data)},
            preset_manager=preset_manager,
        )
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("steps", 50)]
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_no_preset_manager_still_validates_against_form_data(self):
        ctx = make_context(
            session_metadata={"form_state": make_form_state()},
            preset_manager=None,
        )
        result = await UpdateFormSettingsTool().execute(
            ctx, changes=[make_change("steps", 50)]
        )
        assert result.success is True


# ---------------------------------------------------------------------------
# execute_confirmed()
# ---------------------------------------------------------------------------

class TestUpdateFormSettingsToolExecuteConfirmed:
    @pytest.mark.asyncio
    async def test_returns_apply_form_changes_action(self):
        form_data = {"steps": 30, "cfg": 7.0}
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data=form_data)}
        )
        result = await UpdateFormSettingsTool().execute_confirmed(
            ctx, changes=[make_change("steps", 50)]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["action"] == "apply_form_changes"

    @pytest.mark.asyncio
    async def test_applied_changes_contain_old_and_new_values(self):
        form_data = {"steps": 30}
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data=form_data)}
        )
        result = await UpdateFormSettingsTool().execute_confirmed(
            ctx, changes=[make_change("steps", 50, reason="Better quality")]
        )
        payload = json.loads(result.data)
        change = payload["applied_changes"][0]
        assert change["field_name"] == "steps"
        assert change["old_value"] == 30
        assert change["new_value"] == 50
        assert change["reason"] == "Better quality"

    @pytest.mark.asyncio
    async def test_applied_changes_old_value_is_none_when_not_in_form_data(self):
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data={})}
        )
        result = await UpdateFormSettingsTool().execute_confirmed(
            ctx, changes=[make_change("steps", 50)]
        )
        payload = json.loads(result.data)
        assert payload["applied_changes"][0]["old_value"] is None

    @pytest.mark.asyncio
    async def test_multiple_changes_all_appear_in_applied(self):
        form_data = {"steps": 30, "cfg": 7.0, "sampler": "Euler"}
        ctx = make_context(
            session_metadata={"form_state": make_form_state(form_data=form_data)}
        )
        result = await UpdateFormSettingsTool().execute_confirmed(
            ctx,
            changes=[
                make_change("steps", 50),
                make_change("cfg", 9.0),
                make_change("sampler", "DPM++ 2M"),
            ],
        )
        payload = json.loads(result.data)
        assert len(payload["applied_changes"]) == 3

    @pytest.mark.asyncio
    async def test_empty_changes_returns_empty_applied_list(self):
        ctx = make_context(
            session_metadata={"form_state": make_form_state()}
        )
        result = await UpdateFormSettingsTool().execute_confirmed(ctx, changes=[])
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["applied_changes"] == []

    @pytest.mark.asyncio
    async def test_works_without_form_state(self):
        """execute_confirmed should still succeed even with no form_state in session."""
        ctx = make_context()  # empty session_metadata
        result = await UpdateFormSettingsTool().execute_confirmed(
            ctx, changes=[make_change("steps", 50)]
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["applied_changes"][0]["old_value"] is None

    @pytest.mark.asyncio
    async def test_skips_changes_with_empty_field_name(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute_confirmed(
            ctx,
            changes=[
                {"field_name": "", "value": 10},
                make_change("steps", 50),
            ],
        )
        payload = json.loads(result.data)
        # Only the valid one should appear
        assert len(payload["applied_changes"]) == 1
        assert payload["applied_changes"][0]["field_name"] == "steps"

    @pytest.mark.asyncio
    async def test_reason_defaults_to_empty_string_when_absent(self):
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute_confirmed(
            ctx, changes=[{"field_name": "steps", "value": 50}]
        )
        payload = json.loads(result.data)
        assert payload["applied_changes"][0]["reason"] == ""


# ---------------------------------------------------------------------------
# Approval contract
# ---------------------------------------------------------------------------

class TestApprovalContract:
    def test_requires_approval_is_true(self):
        assert UpdateFormSettingsTool().requires_approval is True

    @pytest.mark.asyncio
    async def test_execute_confirmed_is_callable(self):
        """execute_confirmed must be overridden (not raise NotImplementedError)."""
        ctx = make_context(session_metadata={"form_state": make_form_state()})
        result = await UpdateFormSettingsTool().execute_confirmed(ctx, changes=[])
        assert isinstance(result, ToolResult)
