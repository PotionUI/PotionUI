"""Tests for GetPresetInfoTool's preset_id resolution order."""

import json
import pytest
from unittest.mock import MagicMock

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin import GetPresetInfoTool


def make_context(**kwargs) -> ToolContext:
    return ToolContext(user_id="user-test", **kwargs)


def make_preset_data(preset_id: str, llm: dict = None) -> dict:
    return {
        "preset": {
            "id": preset_id,
            "name": "Some Preset",
            "description": "",
            "modes": [],
            "llm": llm or {},
        }
    }


class TestGetPresetInfoResolutionOrder:
    def _tool(self):
        return GetPresetInfoTool()

    @pytest.mark.asyncio
    async def test_no_arg_resolves_via_form_state(self):
        """The live key the chat populates is session_metadata['form_state']['preset']."""
        pm = MagicMock()
        pm.get_preset.return_value = make_preset_data("p-form-state")
        ctx = make_context(
            preset_manager=pm,
            session_metadata={"form_state": {"preset": "p-form-state", "mode": "t2i"}},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        pm.get_preset.assert_called_once_with("p-form-state")
        data = json.loads(result.data)
        assert data["id"] == "p-form-state"

    @pytest.mark.asyncio
    async def test_falls_back_to_legacy_session_metadata_preset_id(self):
        """When form_state has no preset, fall back to the legacy top-level key."""
        pm = MagicMock()
        pm.get_preset.return_value = make_preset_data("p-legacy")
        ctx = make_context(
            preset_manager=pm,
            session_metadata={"preset_id": "p-legacy"},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        pm.get_preset.assert_called_once_with("p-legacy")

    @pytest.mark.asyncio
    async def test_falls_back_to_legacy_when_form_state_has_no_preset_key(self):
        """form_state present but without a 'preset' entry still falls through to legacy key."""
        pm = MagicMock()
        pm.get_preset.return_value = make_preset_data("p-legacy-2")
        ctx = make_context(
            preset_manager=pm,
            session_metadata={
                "form_state": {"mode": "t2i"},
                "preset_id": "p-legacy-2",
            },
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        pm.get_preset.assert_called_once_with("p-legacy-2")

    @pytest.mark.asyncio
    async def test_explicit_preset_id_wins_over_both_session_keys(self):
        pm = MagicMock()
        pm.get_preset.return_value = make_preset_data("p-explicit")
        ctx = make_context(
            preset_manager=pm,
            session_metadata={
                "form_state": {"preset": "p-form-state"},
                "preset_id": "p-legacy",
            },
        )

        result = await self._tool().execute(ctx, preset_id="p-explicit")

        assert result.success is True
        pm.get_preset.assert_called_once_with("p-explicit")

    @pytest.mark.asyncio
    async def test_form_state_preset_wins_over_legacy_key_when_both_present(self):
        pm = MagicMock()
        pm.get_preset.return_value = make_preset_data("p-form-state")
        ctx = make_context(
            preset_manager=pm,
            session_metadata={
                "form_state": {"preset": "p-form-state"},
                "preset_id": "p-legacy",
            },
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        pm.get_preset.assert_called_once_with("p-form-state")

    @pytest.mark.asyncio
    async def test_neither_present_gives_human_error(self):
        pm = MagicMock()
        ctx = make_context(preset_manager=pm, session_metadata={})

        result = await self._tool().execute(ctx)

        assert result.success is False
        assert "No preset_id" in result.error
        assert "session metadata" in result.error

    @pytest.mark.asyncio
    async def test_neither_present_gives_human_error_with_empty_form_state(self):
        pm = MagicMock()
        ctx = make_context(preset_manager=pm, session_metadata={"form_state": {}})

        result = await self._tool().execute(ctx)

        assert result.success is False
        assert "No preset_id" in result.error


class TestGetPresetInfoLLMGuideModeResolution:
    """`llm.modes[<current mode>]` (see docs/presets.md "LLM context") replaces
    `llm.guide` in the `llm_guide` summary field when the tool can resolve the
    active mode from form_state."""

    def _tool(self):
        return GetPresetInfoTool()

    @pytest.mark.asyncio
    async def test_base_guide_surfaced_with_no_modes_block(self):
        pm = MagicMock()
        pm.get_preset.return_value = make_preset_data("p1", llm={"guide": "Base guide."})
        ctx = make_context(
            preset_manager=pm,
            session_metadata={"form_state": {"preset": "p1", "mode": "txt2img"}},
        )

        result = await self._tool().execute(ctx)

        data = json.loads(result.data)
        assert data["llm_guide"] == "Base guide."
        assert "llm_guide_modes" not in data

    @pytest.mark.asyncio
    async def test_current_mode_override_replaces_base_guide(self):
        pm = MagicMock()
        pm.get_preset.return_value = make_preset_data("p1", llm={
            "guide": "Base guide.",
            "modes": {"refs": {"guide": "Refs guide: six-section brief."}},
        })
        ctx = make_context(
            preset_manager=pm,
            session_metadata={"form_state": {"preset": "p1", "mode": "refs"}},
        )

        result = await self._tool().execute(ctx)

        data = json.loads(result.data)
        assert data["llm_guide"] == "Refs guide: six-section brief."
        assert "llm_guide_modes" not in data

    @pytest.mark.asyncio
    async def test_unresolved_mode_keeps_base_guide_and_lists_overrides(self):
        pm = MagicMock()
        pm.get_preset.return_value = make_preset_data("p1", llm={
            "guide": "Base guide.",
            "modes": {"refs": {"guide": "Refs guide."}, "video": {"guide": "Video guide."}},
        })
        # No 'mode' key in form_state at all - the tool cannot resolve one.
        ctx = make_context(
            preset_manager=pm,
            session_metadata={"form_state": {"preset": "p1"}},
        )

        result = await self._tool().execute(ctx)

        data = json.loads(result.data)
        assert data["llm_guide"] == "Base guide."
        assert set(data["llm_guide_modes"]) == {"refs", "video"}

    @pytest.mark.asyncio
    async def test_explicit_preset_id_does_not_borrow_form_state_mode(self):
        """An explicit preset_id argument may point at a different preset than
        the active form - its mode override must not leak across presets."""
        pm = MagicMock()
        pm.get_preset.return_value = make_preset_data("p-other", llm={
            "guide": "Other preset base guide.",
            "modes": {"refs": {"guide": "Should not apply here."}},
        })
        ctx = make_context(
            preset_manager=pm,
            session_metadata={"form_state": {"preset": "p1", "mode": "refs"}},
        )

        result = await self._tool().execute(ctx, preset_id="p-other")

        data = json.loads(result.data)
        assert data["llm_guide"] == "Other preset base guide."
        assert set(data["llm_guide_modes"]) == {"refs"}
