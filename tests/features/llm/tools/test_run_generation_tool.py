"""Tests for RunGenerationTool."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from typing import Any

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.builtin.run_generation_tool import RunGenerationTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_context(
    user_id: str = "user-1",
    session_metadata: dict = None,
    generation_orchestrator: Any = None,
) -> ToolContext:
    return ToolContext(
        user_id=user_id,
        session_metadata=session_metadata or {},
        generation_orchestrator=generation_orchestrator,
    )


def make_form_state(
    preset: str = "preset-sdxl",
    mode: str = "txt2img",
    form_data: dict = None,
) -> dict:
    return {
        "preset": preset,
        "mode": mode,
        "form_data": form_data if form_data is not None else {
            "steps": 30,
            "cfg": 7.0,
            "width": 1024,
            "height": 1024,
            "sampler": "DPM++ 2M",
            "prompt": "a beautiful landscape",
            "negative_prompt": "blurry, ugly",
        },
    }


def make_segment(content: str, seg_type: str = "positive", is_disabled: bool = False) -> dict:
    return {
        "content": content,
        "type": seg_type,
        "isDisabled": is_disabled,
    }


def make_orchestrator(generation_id: str = "gen-123") -> AsyncMock:
    orchestrator = AsyncMock()
    orchestrator.start_generation.return_value = {
        "generation_id": generation_id,
        "status": {"status": "pending"},
    }
    return orchestrator


# ---------------------------------------------------------------------------
# Schema / metadata
# ---------------------------------------------------------------------------

class TestRunGenerationToolSchema:
    def test_name(self):
        assert RunGenerationTool().name == "run_generation"

    def test_hint_is_nonempty(self):
        assert len(RunGenerationTool().hint) > 0

    def test_description_is_nonempty(self):
        assert len(RunGenerationTool().description) > 0

    def test_requires_approval(self):
        assert RunGenerationTool().requires_approval is True

    def test_parameters_has_no_required_fields(self):
        schema = RunGenerationTool().parameters
        assert schema.get("required", []) == []

    def test_parameters_has_override_values(self):
        schema = RunGenerationTool().parameters
        assert "override_values" in schema["properties"]

    def test_to_schema_structure(self):
        schema = RunGenerationTool().to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "run_generation"
        assert "parameters" in schema["function"]


# ---------------------------------------------------------------------------
# execute() — preview / proposal phase
# ---------------------------------------------------------------------------

class TestRunGenerationToolExecute:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_form_state(self):
        ctx = make_context()
        result = await RunGenerationTool().execute(ctx)
        assert result.success is False
        assert "form state" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_preset_id_missing(self):
        form_state = make_form_state(preset="")
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        assert result.success is False
        assert "preset" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_preview_with_valid_form_state(self):
        form_state = make_form_state()
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["action"] == "Start generation"
        assert payload["preset_id"] == "preset-sdxl"
        assert payload["mode"] == "txt2img"

    @pytest.mark.asyncio
    async def test_preview_uses_segments_for_prompt(self):
        form_state = make_form_state()
        segments = [
            make_segment("golden hour"),
            make_segment("sharp focus"),
        ]
        ctx = make_context(session_metadata={"form_state": form_state, "segments": segments})
        result = await RunGenerationTool().execute(ctx)
        assert result.success is True
        payload = json.loads(result.data)
        assert "golden hour" in payload["prompt"]
        assert "sharp focus" in payload["prompt"]

    @pytest.mark.asyncio
    async def test_preview_skips_disabled_segments(self):
        form_state = make_form_state()
        segments = [
            make_segment("visible segment"),
            make_segment("disabled content", is_disabled=True),
        ]
        ctx = make_context(session_metadata={"form_state": form_state, "segments": segments})
        result = await RunGenerationTool().execute(ctx)
        payload = json.loads(result.data)
        assert "disabled content" not in payload["prompt"]
        assert "visible segment" in payload["prompt"]

    @pytest.mark.asyncio
    async def test_preview_separates_negative_segments(self):
        form_state = make_form_state(form_data={})
        segments = [
            make_segment("epic hero", seg_type="positive"),
            make_segment("blurry", seg_type="negative"),
        ]
        ctx = make_context(session_metadata={"form_state": form_state, "segments": segments})
        result = await RunGenerationTool().execute(ctx)
        payload = json.loads(result.data)
        assert "epic hero" in payload["prompt"]
        assert "blurry" in payload.get("negative_prompt", "")

    @pytest.mark.asyncio
    async def test_preview_falls_back_to_form_data_prompt_when_no_segments(self):
        form_state = make_form_state(form_data={"prompt": "from form data"})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        payload = json.loads(result.data)
        assert "from form data" in payload["prompt"]

    @pytest.mark.asyncio
    async def test_preview_includes_key_settings_from_form_data(self):
        form_state = make_form_state(form_data={
            "steps": 25,
            "cfg": 6.5,
            "width": 768,
            "height": 1024,
        })
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        payload = json.loads(result.data)
        settings = payload.get("settings", {})
        assert settings["steps"] == 25
        assert settings["cfg"] == 6.5
        assert settings["width"] == 768
        assert settings["height"] == 1024

    @pytest.mark.asyncio
    async def test_preview_omits_settings_key_when_no_key_fields_present(self):
        form_state = make_form_state(form_data={"some_custom_field": "value"})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        payload = json.loads(result.data)
        assert "settings" not in payload

    @pytest.mark.asyncio
    async def test_override_values_appear_in_preview(self):
        form_state = make_form_state(form_data={"steps": 30, "width": 512})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx, override_values={"width": 1024, "height": 1024})
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["overrides_applied"]["width"] == 1024
        assert payload["overrides_applied"]["height"] == 1024

    @pytest.mark.asyncio
    async def test_overrides_merge_into_settings_preview(self):
        form_state = make_form_state(form_data={"steps": 30, "width": 512})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx, override_values={"width": 1024})
        payload = json.loads(result.data)
        assert payload["settings"]["width"] == 1024

    @pytest.mark.asyncio
    async def test_override_values_none_does_not_error(self):
        form_state = make_form_state()
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx, override_values=None)
        assert result.success is True

    @pytest.mark.asyncio
    async def test_prompt_truncated_at_300_chars(self):
        long_prompt = "a" * 400
        form_state = make_form_state(form_data={"prompt": long_prompt})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        payload = json.loads(result.data)
        assert payload["prompt"].endswith("...")
        assert len(payload["prompt"]) == 303  # 300 + "..."

    @pytest.mark.asyncio
    async def test_short_prompt_not_truncated(self):
        form_state = make_form_state(form_data={"prompt": "short prompt"})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        payload = json.loads(result.data)
        assert not payload["prompt"].endswith("...")

    @pytest.mark.asyncio
    async def test_mode_defaults_to_txt2img_when_absent(self):
        form_state = {"preset": "preset-x", "form_data": {}}
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        payload = json.loads(result.data)
        assert payload["mode"] == "txt2img"

    @pytest.mark.asyncio
    async def test_execute_does_not_start_generation(self):
        orchestrator = make_orchestrator()
        form_state = make_form_state()
        ctx = make_context(
            session_metadata={"form_state": form_state},
            generation_orchestrator=orchestrator,
        )
        await RunGenerationTool().execute(ctx)
        orchestrator.start_generation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_negative_prompt_omitted_when_empty(self):
        form_state = make_form_state(form_data={"prompt": "good stuff"})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        payload = json.loads(result.data)
        assert "negative_prompt" not in payload


# ---------------------------------------------------------------------------
# execute() — structured approval preview (.preview)
# ---------------------------------------------------------------------------

class TestRunGenerationToolApprovalPreview:
    @pytest.mark.asyncio
    async def test_preview_kind_and_summary(self):
        form_state = make_form_state(form_data={"prompt": "a beautiful landscape"})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        assert result.preview is not None
        assert result.preview.kind == "generation"
        assert result.preview.summary.startswith("a beautiful landscape")

    @pytest.mark.asyncio
    async def test_preview_fields_carry_preset_mode_and_settings(self):
        form_state = make_form_state(
            preset="preset-sdxl", mode="txt2img",
            form_data={"width": 1024, "height": 1024, "steps": 30, "cfg": 7.0,
                       "sampler": "DPM++ 2M", "seed": 42},
        )
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        labels = {f["label"]: f["value"] for f in result.preview.fields}
        assert labels["Preset"] == "preset-sdxl"
        assert labels["Mode"] == "txt2img"
        assert labels["Resolution"] == "1024×1024"
        assert labels["Steps"] == "30"
        assert labels["CFG"] == "7.0"
        assert labels["Sampler"] == "DPM++ 2M"
        assert labels["Seed"] == "42"
        seed_field = next(f for f in result.preview.fields if f["label"] == "Seed")
        assert seed_field.get("mono") is True

    @pytest.mark.asyncio
    async def test_override_flags_old_value_on_changed_field(self):
        form_state = make_form_state(form_data={"steps": 20, "width": 512, "height": 512})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx, override_values={"steps": 50})
        steps_field = next(f for f in result.preview.fields if f["label"] == "Steps")
        assert steps_field["value"] == "50"
        assert steps_field["old"] == "20"
        # A field that was never overridden must not carry "old".
        width_field = next(f for f in result.preview.fields if f["label"] == "Resolution")
        assert "old" not in width_field

    @pytest.mark.asyncio
    async def test_text_blocks_hold_full_untruncated_prompt(self):
        long_prompt = "a" * 400
        form_state = make_form_state(form_data={"prompt": long_prompt, "negative_prompt": "blurry"})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        prompt_block = next(b for b in result.preview.text_blocks if b["label"] == "Prompt")
        assert prompt_block["text"] == long_prompt  # unlike `data`, never truncated at 300 chars
        negative_block = next(b for b in result.preview.text_blocks if b["label"] == "Negative prompt")
        assert negative_block["text"] == "blurry"

    @pytest.mark.asyncio
    async def test_negative_prompt_block_omitted_when_empty(self):
        form_state = make_form_state(form_data={"prompt": "good stuff"})
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute(ctx)
        assert [b["label"] for b in result.preview.text_blocks] == ["Prompt"]


# ---------------------------------------------------------------------------
# execute_confirmed() — mutation phase
# ---------------------------------------------------------------------------

class TestRunGenerationToolExecuteConfirmed:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_orchestrator(self):
        form_state = make_form_state()
        ctx = make_context(session_metadata={"form_state": form_state})
        result = await RunGenerationTool().execute_confirmed(ctx)
        assert result.success is False
        assert "orchestrator" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_no_form_state(self):
        orchestrator = make_orchestrator()
        ctx = make_context(generation_orchestrator=orchestrator)
        result = await RunGenerationTool().execute_confirmed(ctx)
        assert result.success is False
        assert "form state" in result.error.lower()

    @pytest.mark.asyncio
    async def test_successful_confirmed_returns_generation_id(self):
        form_state = make_form_state()
        orchestrator = make_orchestrator(generation_id="gen-abc")
        ctx = make_context(
            session_metadata={"form_state": form_state},
            generation_orchestrator=orchestrator,
        )
        result = await RunGenerationTool().execute_confirmed(ctx)
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["generation_id"] == "gen-abc"

    @pytest.mark.asyncio
    async def test_confirmed_calls_orchestrator_with_user_id(self):
        form_state = make_form_state()
        orchestrator = make_orchestrator()
        ctx = make_context(
            user_id="user-99",
            session_metadata={"form_state": form_state},
            generation_orchestrator=orchestrator,
        )
        await RunGenerationTool().execute_confirmed(ctx)
        call_kwargs = orchestrator.start_generation.call_args[1]
        assert call_kwargs["user_id"] == "user-99"

    @pytest.mark.asyncio
    async def test_confirmed_calls_orchestrator_with_request(self):
        form_state = make_form_state(preset="my-preset", mode="img2img")
        orchestrator = make_orchestrator()
        ctx = make_context(
            session_metadata={"form_state": form_state},
            generation_orchestrator=orchestrator,
        )
        await RunGenerationTool().execute_confirmed(ctx)
        call_kwargs = orchestrator.start_generation.call_args[1]
        request = call_kwargs["request"]
        assert request.preset_id == "my-preset"
        assert request.mode == "img2img"

    @pytest.mark.asyncio
    async def test_confirmed_builds_prompt_from_segments(self):
        form_state = make_form_state(form_data={})
        segments = [
            make_segment("majestic mountains"),
            make_segment("low quality", seg_type="negative"),
        ]
        orchestrator = make_orchestrator()
        ctx = make_context(
            session_metadata={"form_state": form_state, "segments": segments},
            generation_orchestrator=orchestrator,
        )
        await RunGenerationTool().execute_confirmed(ctx)
        call_kwargs = orchestrator.start_generation.call_args[1]
        request = call_kwargs["request"]
        assert request.prompts is not None
        assert "majestic mountains" in request.prompts[0].positive
        assert "low quality" in request.prompts[0].negative

    @pytest.mark.asyncio
    async def test_confirmed_applies_override_values(self):
        form_state = make_form_state(form_data={"steps": 20})
        orchestrator = make_orchestrator()
        ctx = make_context(
            session_metadata={"form_state": form_state},
            generation_orchestrator=orchestrator,
        )
        await RunGenerationTool().execute_confirmed(ctx, override_values={"steps": 50})
        call_kwargs = orchestrator.start_generation.call_args[1]
        request = call_kwargs["request"]
        assert request.form_data["steps"] == 50

    @pytest.mark.asyncio
    async def test_confirmed_returns_status_from_orchestrator(self):
        form_state = make_form_state()
        orchestrator = AsyncMock()
        orchestrator.start_generation.return_value = {
            "generation_id": "gen-xyz",
            "status": {"status": "running"},
        }
        ctx = make_context(
            session_metadata={"form_state": form_state},
            generation_orchestrator=orchestrator,
        )
        result = await RunGenerationTool().execute_confirmed(ctx)
        payload = json.loads(result.data)
        assert payload["status"] == "running"
        assert payload["generation_id"] == "gen-xyz"

    @pytest.mark.asyncio
    async def test_orchestrator_exception_returns_failure(self):
        form_state = make_form_state()
        orchestrator = AsyncMock()
        orchestrator.start_generation.side_effect = RuntimeError("backend unavailable")
        ctx = make_context(
            session_metadata={"form_state": form_state},
            generation_orchestrator=orchestrator,
        )
        result = await RunGenerationTool().execute_confirmed(ctx)
        assert result.success is False
        assert "failed" in result.error.lower() or "backend unavailable" in result.error.lower()

    @pytest.mark.asyncio
    async def test_confirmed_awaits_orchestrator_exactly_once(self):
        form_state = make_form_state()
        orchestrator = make_orchestrator()
        ctx = make_context(
            session_metadata={"form_state": form_state},
            generation_orchestrator=orchestrator,
        )
        await RunGenerationTool().execute_confirmed(ctx)
        orchestrator.start_generation.assert_awaited_once()
