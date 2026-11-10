import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.features.forms.binding import FormBindingError
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.form_context_tool import GetFormStateTool
from src.features.llm.tools.builtin.preset_info_tool import GetPresetInfoTool
from src.features.llm.tools.builtin.regional_prompt_tool import SetRegionalPromptTool
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.mcp.protocol import EXCLUDED_TOOL_NAMES
from src.features.presets import PresetTemplateLoader
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.regional_prompt import offers_regional_prompt, regional_prompt_guide, regional_prompt_modes

OFFERING_VARS = {"regional_prompt": {"modes": ["txt2img"], "size_field": "resolution"}}
REGION = {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4, "description": 'Sign "OPEN" in neon'}


def _collaborators(preset_vars):
    preset = SimpleNamespace(vars=preset_vars, modes={"txt2img": object(), "inpaint": object()})
    return SimpleNamespace(file_repo=SimpleNamespace(find_preset_by_id=lambda preset_id: preset))


def _chat_context(preset_vars=OFFERING_VARS, mode="txt2img", form_data=None):
    form_state = {"preset": "p1", "mode": mode, "form_data": form_data or {}}
    return ToolContext(
        user_id="u1", preset_collaborators=_collaborators(preset_vars),
        session_metadata={"form_state": form_state},
    )


class TestHelpers:
    def test_modes_and_offer(self):
        assert regional_prompt_modes(OFFERING_VARS) == ["txt2img"]
        assert offers_regional_prompt(OFFERING_VARS, "txt2img")
        assert not offers_regional_prompt(OFFERING_VARS, "inpaint")
        assert not offers_regional_prompt(None, "txt2img")
        assert not offers_regional_prompt({"regional_prompt": "bad"}, "txt2img")

    def test_guide_only_for_declaring_presets(self):
        assert regional_prompt_guide({}) is None
        assert regional_prompt_guide(OFFERING_VARS)["modes"] == ["txt2img"]


class TestSetRegionalPrompt:
    def test_contract(self):
        tool = SetRegionalPromptTool()
        assert tool.name == "set_regional_prompt"
        assert tool.requires_approval is True
        assert tool.parameters["required"] == ["regions"]
        assert "set_regional_prompt" in EXCLUDED_TOOL_NAMES

    @pytest.mark.asyncio
    async def test_unoffered_preset_is_refused(self):
        result = await SetRegionalPromptTool().execute(_chat_context({}), regions=[REGION])
        assert result.success is False
        assert "does not offer regional prompting" in result.error

    @pytest.mark.asyncio
    async def test_unoffered_mode_names_the_offering_modes(self):
        result = await SetRegionalPromptTool().execute(_chat_context(mode="inpaint"), regions=[REGION])
        assert result.success is False
        assert "txt2img" in result.error

    @pytest.mark.asyncio
    async def test_no_form_is_refused(self):
        result = await SetRegionalPromptTool().execute(ToolContext(user_id="u1"), regions=[REGION])
        assert result.success is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("regions", [
        "nope",
        [{**REGION, "w": 0}],
        [{**REGION, "x": "left"}],
        [{**REGION, "description": "  "}],
        ["box"],
    ])
    async def test_bad_regions_are_refused(self, regions):
        result = await SetRegionalPromptTool().execute(_chat_context(), regions=regions)
        assert result.success is False
        assert result.error

    @pytest.mark.asyncio
    async def test_valid_regions_give_preview_rows(self):
        result = await SetRegionalPromptTool().execute(_chat_context(), regions=[REGION])
        assert result.success is True
        preview = result.preview
        assert (preview.action, preview.kind) == ("Set regional prompt", "regions")
        assert preview.rows == [{"position": "10%, 20%", "size": "50% × 40%", "text": 'Sign "OPEN" in neon'}]

    @pytest.mark.asyncio
    async def test_confirmed_returns_the_action_payload(self):
        result = await SetRegionalPromptTool().execute_confirmed(_chat_context(), regions=[REGION])
        assert json.loads(result.data) == {
            "action": "set_regional_prompt",
            "regional_prompt": {
                "enabled": True,
                "items": [{"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4, "caption": 'Sign "OPEN" in neon'}],
            },
        }

    @pytest.mark.asyncio
    async def test_enabled_false_keeps_the_regions(self):
        result = await SetRegionalPromptTool().execute_confirmed(_chat_context(), regions=[REGION], enabled=False)
        document = json.loads(result.data)["regional_prompt"]
        assert document["enabled"] is False
        assert len(document["items"]) == 1


@pytest.fixture(scope="module")
def marketplace_file_repo():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    return FilePresetRepository(loader)


class TestPresetInfoSection:
    def _context(self, file_repo):
        with patch("src.features.presets.collaborators.PresetFormSerializer"):
            collaborators = PresetCollaborators(
                preset_loader=Mock(), preset_processor=Mock(), template_processor=Mock(), file_repo=file_repo,
                db_repo=Mock(), user_repo=Mock(), group_repo=Mock(), pipeline_builder=Mock(),
                pipe_catalog=Mock(), plugins=Mock(), settings=Mock(),
            )
        return ToolContext(user_id="u1", is_admin=True, preset_collaborators=collaborators)

    @pytest.mark.asyncio
    async def test_section_present_for_a_declaring_preset(self, marketplace_file_repo):
        result = await GetPresetInfoTool().execute(self._context(marketplace_file_repo), preset_id="01M52YQH5XRAH60J82RY5NTM2V")
        assert result.success is True, result.error
        data = json.loads(result.data)
        assert data["regional_prompt"]["modes"] == ["txt2img"]
        assert "form_overrides.regional_prompt" in data["regional_prompt"]["how"]
        assert "set_regional_prompt" in data["llm_guide"]

    @pytest.mark.asyncio
    async def test_section_absent_otherwise(self, marketplace_file_repo):
        sdxl = next(p for p in marketplace_file_repo.preset_loader.presets if p.name == "SDXL")
        result = await GetPresetInfoTool().execute(self._context(marketplace_file_repo), preset_id=sdxl.id)
        assert result.success is True, result.error
        assert "regional_prompt" not in json.loads(result.data)


class TestFormState:
    @pytest.mark.asyncio
    async def test_current_regions_are_reported(self):
        document = {"enabled": True, "items": [{"x": 0, "y": 0, "w": 1, "h": 1, "caption": "sky"}]}
        result = await GetFormStateTool().execute(_chat_context(form_data={"regional_prompt": document}))
        data = json.loads(result.data)
        assert data["regional_prompt"] == document
        assert "regional_prompt" not in data.get("fields", {})


class TestStartGeneration:
    def _context(self, preset_vars, orchestrator=None):
        return ToolContext(
            user_id="u1", preset_collaborators=_collaborators(preset_vars),
            generation_orchestrator=orchestrator or AsyncMock(),
        )

    @pytest.mark.asyncio
    async def test_valid_regional_prompt_reaches_the_orchestrator(self):
        orchestrator = AsyncMock()
        orchestrator.start_generation.return_value = {"generation_id": "g1", "status": {"status": "pending"}}
        document = {"enabled": True, "items": [{"x": 0, "y": 0, "w": 0.5, "h": 0.5, "caption": "a cat"}]}
        result = await StartGenerationTool().execute_confirmed(
            self._context(OFFERING_VARS, orchestrator), preset_id="p1", mode="txt2img", prompt="x",
            form_overrides={"regional_prompt": document},
        )
        assert result.success is True, result.error
        request = orchestrator.start_generation.call_args.kwargs["request"]
        assert request.form_data["regional_prompt"] == document

    @pytest.mark.asyncio
    async def test_malformed_document_is_refused_in_preview_and_on_confirm(self):
        bad = {"enabled": True, "items": [{"x": 0, "y": 0, "w": 0, "h": 0.5, "caption": "a"}]}
        for call in (StartGenerationTool().execute, StartGenerationTool().execute_confirmed):
            result = await call(
                self._context(OFFERING_VARS), preset_id="p1", mode="txt2img", prompt="x",
                form_overrides={"regional_prompt": bad},
            )
            assert result.success is False
            assert "regional_prompt is invalid" in result.error

    @pytest.mark.asyncio
    async def test_override_for_a_preset_that_does_not_offer_it_is_refused(self):
        result = await StartGenerationTool().execute(
            self._context({}), preset_id="p1", mode="txt2img", prompt="x",
            form_overrides={"regional_prompt": {"enabled": True, "items": []}},
        )
        assert result.success is False
        assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_binding_error_comes_back_readable(self):
        orchestrator = AsyncMock()
        orchestrator.start_generation.side_effect = FormBindingError(
            ["regional_prompt: items must be a list"], {"regional_prompt": ["items must be a list"]}
        )
        result = await StartGenerationTool().execute_confirmed(
            self._context(OFFERING_VARS, orchestrator), preset_id="p1", mode="txt2img", prompt="x",
            form_overrides={"seed": 1},
        )
        assert result.success is False
        assert "regional_prompt: items must be a list" in result.error
        assert "unexpectedly" not in result.error
