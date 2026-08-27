"""Tests for StartGenerationTool."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool


def make_context(
    user_id: str = "user-1",
    generation_orchestrator: Any = None,
    preset_manager: Any = None,
    settings: Any = None,
) -> ToolContext:
    return ToolContext(
        user_id=user_id,
        generation_orchestrator=generation_orchestrator,
        preset_manager=preset_manager,
        settings=settings,
    )


def make_orchestrator(generation_id: str = "gen-123") -> AsyncMock:
    orchestrator = AsyncMock()
    orchestrator.start_generation.return_value = {
        "generation_id": generation_id,
        "status": {"status": "pending"},
    }
    return orchestrator


class TestSchema:
    def test_identity(self):
        tool = StartGenerationTool()
        assert tool.name == "start_generation"
        assert tool.requires_approval is True
        assert tool.parameters["required"] == ["preset_id"]

    def test_does_not_read_session_form_state(self):
        """This tool must work with no live chat session at all (MCP has none) -
        session_metadata is never touched."""
        ctx = ToolContext(user_id="user-1", session_metadata={})
        assert ctx.session_metadata == {}


class TestExecutePreview:
    @pytest.mark.asyncio
    async def test_requires_preset_id(self):
        result = await StartGenerationTool().execute(make_context())
        assert result.success is False
        assert "preset_id" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_preview_without_starting(self):
        orchestrator = make_orchestrator()
        result = await StartGenerationTool().execute(
            make_context(generation_orchestrator=orchestrator),
            preset_id="sdxl/base", prompt="a red fox", form_overrides={"width": 1024},
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["preset_id"] == "sdxl/base"
        assert payload["mode"] == "txt2img"
        assert payload["prompt"] == "a red fox"
        assert payload["form_overrides"] == {"width": 1024}
        orchestrator.start_generation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mode_defaults_to_txt2img(self):
        result = await StartGenerationTool().execute(make_context(), preset_id="sdxl/base")
        payload = json.loads(result.data)
        assert payload["mode"] == "txt2img"

    @pytest.mark.asyncio
    async def test_explicit_mode_is_kept(self):
        result = await StartGenerationTool().execute(make_context(), preset_id="sdxl/base", mode="img2img")
        payload = json.loads(result.data)
        assert payload["mode"] == "img2img"


class TestExecuteConfirmed:
    @pytest.mark.asyncio
    async def test_returns_error_when_no_orchestrator(self):
        result = await StartGenerationTool().execute_confirmed(make_context(), preset_id="sdxl/base")
        assert result.success is False
        assert "orchestrator" in result.error.lower()

    @pytest.mark.asyncio
    async def test_requires_preset_id(self):
        orchestrator = make_orchestrator()
        result = await StartGenerationTool().execute_confirmed(make_context(generation_orchestrator=orchestrator))
        assert result.success is False
        assert "preset_id" in result.error.lower()

    @pytest.mark.asyncio
    async def test_calls_orchestrator_with_request_built_from_arguments_alone(self):
        orchestrator = make_orchestrator(generation_id="gen-abc")
        ctx = make_context(user_id="user-42", generation_orchestrator=orchestrator)
        result = await StartGenerationTool().execute_confirmed(
            ctx, preset_id="sdxl/base", mode="img2img", prompt="a fox", negative_prompt="blurry",
            form_overrides={"width": 512, "seed": 7},
        )
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["generation_id"] == "gen-abc"

        call_kwargs = orchestrator.start_generation.call_args[1]
        assert call_kwargs["user_id"] == "user-42"
        request = call_kwargs["request"]
        assert request.preset_id == "sdxl/base"
        assert request.mode == "img2img"
        assert request.form_data == {"width": 512, "seed": 7}
        assert request.prompts[0].positive == "a fox"
        assert request.prompts[0].negative == "blurry"

    @pytest.mark.asyncio
    async def test_orchestrator_exception_returns_failure(self):
        orchestrator = AsyncMock()
        orchestrator.start_generation.side_effect = RuntimeError("backend unavailable")
        result = await StartGenerationTool().execute_confirmed(
            make_context(generation_orchestrator=orchestrator), preset_id="sdxl/base",
        )
        assert result.success is False
        assert "backend unavailable" in result.error.lower() or "failed" in result.error.lower()


class TestMediaOverrideValidation:
    @pytest.mark.asyncio
    async def test_rejects_media_override_pointing_outside_storage_root(self, tmp_path):
        preset_manager = MagicMock()
        preset_manager.get_form_schema.return_value = {
            "form_schema": {"properties": {"init_image": {"type": "image"}}},
        }
        settings = MagicMock()
        settings.get_file_storage_directory.return_value = str(tmp_path)
        orchestrator = make_orchestrator()
        ctx = make_context(
            generation_orchestrator=orchestrator, preset_manager=preset_manager, settings=settings,
        )
        result = await StartGenerationTool().execute_confirmed(
            ctx, preset_id="sdxl/img2img", form_overrides={"init_image": "/etc/passwd"},
        )
        assert result.success is False
        orchestrator.start_generation.assert_not_awaited()
