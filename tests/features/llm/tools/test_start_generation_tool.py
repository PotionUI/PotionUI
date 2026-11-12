"""Tests for StartGenerationTool."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool


def make_context(
    user_id: str = "user-1",
    generation_orchestrator: Any = None,
    preset_collaborators: Any = None,
    settings: Any = None,
) -> ToolContext:
    return ToolContext(
        user_id=user_id,
        generation_orchestrator=generation_orchestrator,
        preset_collaborators=preset_collaborators,
        settings=settings,
    )


def make_orchestrator(generation_id: str = "gen-123") -> AsyncMock:
    orchestrator = AsyncMock()
    orchestrator.start_generation.return_value = {
        "generation_id": generation_id,
        "status": {"status": "pending"},
    }
    return orchestrator


class TestAvailability:
    def test_offered_only_without_an_open_form(self):
        tool = StartGenerationTool()
        assert tool.is_available(None) is True
        assert tool.is_available({}) is True
        assert tool.is_available({"preset": "sdxl", "form_data": {"prompt": "a fox"}}) is False


class TestSchema:
    def test_identity(self):
        tool = StartGenerationTool()
        assert tool.name == "start_generation"
        assert tool.requires_approval is True
        assert tool.parameters["required"] == []

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


class TestApprovalPreview:
    @pytest.mark.asyncio
    async def test_preview_kind_and_shared_fields(self):
        result = await StartGenerationTool().execute(
            make_context(),
            preset_id="sdxl/base", mode="img2img", prompt="a red fox",
            form_overrides={"width": 1024, "height": 1024, "steps": 30},
        )
        assert result.preview.kind == "generation"
        labels = {f["label"]: f["value"] for f in result.preview.fields}
        assert labels["Preset"] == "sdxl/base"
        assert labels["Mode"] == "img2img"
        assert labels["Resolution"] == "1024×1024"
        assert labels["Steps"] == "30"
        assert result.preview.summary.startswith("a red fox")
        assert result.preview.text_blocks == [{"label": "Prompt", "text": "a red fox"}]

    @pytest.mark.asyncio
    async def test_preview_never_flags_old_values(self):
        """No live form baseline here -- a field must never claim an 'old' value."""
        result = await StartGenerationTool().execute(
            make_context(), preset_id="sdxl/base", form_overrides={"steps": 50},
        )
        for f in result.preview.fields:
            assert "old" not in f

    @pytest.mark.asyncio
    async def test_preview_omits_fields_not_in_form_overrides(self):
        result = await StartGenerationTool().execute(make_context(), preset_id="sdxl/base")
        labels = {f["label"] for f in result.preview.fields}
        assert "Resolution" not in labels
        assert "Steps" not in labels


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

    @pytest.mark.asyncio
    async def test_a_regular_user_gets_the_safe_summary_never_the_raw_exception(self):
        orchestrator = AsyncMock()
        orchestrator.start_generation.side_effect = FileNotFoundError(2, "No such file or directory", "/srv/models/private/unet.safetensors")
        result = await StartGenerationTool().execute_confirmed(
            make_context(generation_orchestrator=orchestrator), preset_id="sdxl/base",
        )
        assert result.success is False
        assert "A model file this preset needs is missing" in result.error
        for marker in ("/srv/", "Errno", "unet.safetensors"):
            assert marker not in result.error

    @pytest.mark.asyncio
    async def test_an_admin_gets_the_raw_exception(self):
        orchestrator = AsyncMock()
        orchestrator.start_generation.side_effect = FileNotFoundError(2, "No such file or directory", "/srv/models/private/unet.safetensors")
        ctx = ToolContext(user_id="admin-1", is_admin=True, generation_orchestrator=orchestrator)
        result = await StartGenerationTool().execute_confirmed(ctx, preset_id="sdxl/base")
        assert result.success is False
        assert "No such file or directory" in result.error
        assert "/srv/models/private" not in result.error


class TestPresetAssignment:
    @staticmethod
    def _collaborators(assigned):
        collaborators = MagicMock()
        collaborators.db_repo.get_available_preset_ids_for_user.return_value = assigned
        collaborators.file_repo.find_preset_by_id.return_value = MagicMock(modes={"txt2img": {}})
        return collaborators

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["execute", "execute_confirmed"])
    async def test_an_unassigned_regular_user_is_refused(self, method):
        orchestrator = make_orchestrator()
        ctx = make_context(generation_orchestrator=orchestrator, preset_collaborators=self._collaborators([]))

        result = await getattr(StartGenerationTool(), method)(ctx, preset_id="sdxl/base")

        assert result.success is False
        assert "No preset 'sdxl/base'" in result.error
        orchestrator.start_generation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_admin_passes_without_an_assignment(self):
        ctx = ToolContext(user_id="admin-1", is_admin=True, preset_collaborators=self._collaborators([]))

        result = await StartGenerationTool().execute(ctx, preset_id="sdxl/base")

        assert result.success is True

    @pytest.mark.asyncio
    async def test_an_assigned_regular_user_passes(self):
        ctx = make_context(preset_collaborators=self._collaborators(["sdxl/base"]))

        result = await StartGenerationTool().execute(ctx, preset_id="sdxl/base")

        assert result.success is True

class TestMediaOverrideValidation:
    @pytest.mark.asyncio
    async def test_rejects_media_override_pointing_outside_storage_root(self, tmp_path):
        preset_collaborators = MagicMock()
        settings = MagicMock()
        settings.get_file_storage_directory.return_value = str(tmp_path)
        orchestrator = make_orchestrator()
        ctx = make_context(
            generation_orchestrator=orchestrator, preset_collaborators=preset_collaborators, settings=settings,
        )
        with patch(
            "src.features.presets.operations.get_form_schema",
            return_value={"form_schema": {"properties": {"init_image": {"type": "image"}}}},
        ):
            result = await StartGenerationTool().execute_confirmed(
                ctx, preset_id="sdxl/img2img", form_overrides={"init_image": "/etc/passwd"},
            )
        assert result.success is False
        orchestrator.start_generation.assert_not_awaited()


BATCH = [{"positive": "a red fox", "negative": "blurry"}, {"positive": "a grey wolf"}]


class TestBatchPrompts:
    def test_schema_offers_prompts_and_the_description_recommends_it(self):
        tool = StartGenerationTool()
        schema = tool.parameters["properties"]["prompts"]
        assert schema["type"] == "array"
        assert schema["items"]["required"] == ["positive"]
        assert "faster than" in tool.description

    @pytest.mark.asyncio
    async def test_preview_lists_every_prompt(self):
        result = await StartGenerationTool().execute(make_context(), preset_id="sdxl/base", prompts=BATCH)
        assert result.success is True
        payload = json.loads(result.data)
        assert payload["prompts"] == [
            {"positive": "a red fox", "negative": "blurry"},
            {"positive": "a grey wolf", "negative": ""},
        ]
        assert "prompt" not in payload
        assert result.preview.text_blocks[0]["text"] == "1. a red fox\n2. a grey wolf"
        assert result.preview.text_blocks[1] == {"label": "Negative prompt", "text": "1. blurry"}

    @pytest.mark.asyncio
    async def test_prompt_and_prompts_are_mutually_exclusive(self):
        result = await StartGenerationTool().execute(
            make_context(), preset_id="sdxl/base", prompt="a fox", prompts=BATCH,
        )
        assert result.success is False
        assert "not both" in result.error

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [[], "fox", ["fox"], [{"negative": "x"}], [{"positive": "  "}], [{"positive": "a", "negative": 3}]])
    async def test_malformed_prompts_are_rejected(self, bad):
        result = await StartGenerationTool().execute(make_context(), preset_id="sdxl/base", prompts=bad)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_too_many_prompts_are_rejected(self):
        many = [{"positive": f"p{i}"} for i in range(21)]
        result = await StartGenerationTool().execute(make_context(), preset_id="sdxl/base", prompts=many)
        assert result.success is False
        assert "at most 20" in result.error

    @pytest.mark.asyncio
    async def test_confirmed_run_sends_a_batch_request(self):
        orchestrator = make_orchestrator()
        result = await StartGenerationTool().execute_confirmed(
            make_context(generation_orchestrator=orchestrator),
            preset_id="sdxl/base", prompts=BATCH, form_overrides={"quantity": 2},
        )
        assert result.success is True
        request = orchestrator.start_generation.call_args[1]["request"]
        assert request.batch_prompts is True
        assert [(p.positive, p.negative) for p in request.prompts] == [("a red fox", "blurry"), ("a grey wolf", "")]
        assert request.form_data == {"quantity": 2}

    @pytest.mark.asyncio
    async def test_confirmed_single_entry_is_not_a_batch(self):
        orchestrator = make_orchestrator()
        await StartGenerationTool().execute_confirmed(
            make_context(generation_orchestrator=orchestrator), preset_id="sdxl/base", prompts=BATCH[:1],
        )
        request = orchestrator.start_generation.call_args[1]["request"]
        assert request.batch_prompts is False
        assert request.prompts[0].positive == "a red fox"

    @pytest.mark.asyncio
    async def test_plain_prompt_still_sends_no_batch(self):
        orchestrator = make_orchestrator()
        await StartGenerationTool().execute_confirmed(
            make_context(generation_orchestrator=orchestrator), preset_id="sdxl/base", prompt="a fox",
        )
        request = orchestrator.start_generation.call_args[1]["request"]
        assert request.batch_prompts is False

    @pytest.mark.asyncio
    async def test_confirmed_mutual_exclusion_is_rechecked(self):
        orchestrator = make_orchestrator()
        result = await StartGenerationTool().execute_confirmed(
            make_context(generation_orchestrator=orchestrator), preset_id="sdxl/base", prompt="a fox", prompts=BATCH,
        )
        assert result.success is False
        orchestrator.start_generation.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_batch_refusal_reaches_a_regular_user_verbatim(self):
        from src.features.generation.prompt_batch import PromptBatchError

        orchestrator = AsyncMock()
        orchestrator.start_generation.side_effect = PromptBatchError("A batch can produce at most 20 images, but 24.")
        result = await StartGenerationTool().execute_confirmed(
            make_context(generation_orchestrator=orchestrator), preset_id="sdxl/base", prompts=BATCH,
        )
        assert result.success is False
        assert result.error == "A batch can produce at most 20 images, but 24."
