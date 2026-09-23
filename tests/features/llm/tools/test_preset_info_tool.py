import json
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin import GetPresetInfoTool
from src.features.presets import PresetTemplateLoader
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def make_context(**kwargs) -> ToolContext:
    return ToolContext(user_id="user-test", **kwargs)


def make_collaborators(file_repo, db_repo=None) -> PresetCollaborators:
    with patch("src.features.presets.collaborators.PresetFormSerializer"):
        return PresetCollaborators(
            preset_loader=Mock(),
            preset_processor=Mock(),
            template_processor=Mock(),
            file_repo=file_repo,
            db_repo=db_repo or Mock(),
            user_repo=Mock(),
            group_repo=Mock(),
            pipeline_builder=Mock(),
            pipe_catalog=Mock(),
            plugins=Mock(),
            settings=Mock(),
        )


@pytest.fixture(scope="module")
def marketplace_loader():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    return loader


@pytest.fixture(scope="module")
def marketplace_file_repo(marketplace_loader):
    return FilePresetRepository(marketplace_loader)


@pytest.fixture(scope="module")
def sdxl(marketplace_loader):
    matches = [
        p for p in marketplace_loader.presets
        if str(p.path).replace("\\", "/").endswith("presets/marketplace/SDXL")
    ]
    assert len(matches) == 1
    return matches[0]


def _field_by_name(fields, name):
    return next(f for f in fields if f["name"] == name)


class TestGetPresetInfoRealPreset:
    def _tool(self):
        return GetPresetInfoTool()

    @pytest.mark.asyncio
    async def test_valid_id_returns_summary_with_modes_and_tab_nested_fields(
        self, marketplace_file_repo, sdxl
    ):
        collaborators = make_collaborators(marketplace_file_repo)
        ctx = make_context(preset_collaborators=collaborators, is_admin=True, session_metadata={})

        result = await self._tool().execute(ctx, preset_id=sdxl.id)

        assert result.success is True
        data = json.loads(result.data)
        assert data["id"] == sdxl.id
        assert data["name"] == "SDXL"
        assert set(data["modes"]) >= {"txt2img", "inpaint"}
        assert data["mode"] == "txt2img"

        fields = data["form_fields"]
        steps = _field_by_name(fields, "steps")
        assert steps["type"] == "slider"
        assert steps["default"] == 20
        assert steps["min"] == 1
        assert steps["max"] == 50
        assert steps["step"] == 1
        assert "More steps" in steps["ai_hint"]

        sampler = _field_by_name(fields, "sampler")
        assert sampler["options_count"] == 11

    @pytest.mark.asyncio
    async def test_unknown_id_gives_teaching_error(self, marketplace_file_repo):
        collaborators = make_collaborators(marketplace_file_repo)
        ctx = make_context(preset_collaborators=collaborators, is_admin=True, session_metadata={})

        result = await self._tool().execute(ctx, preset_id="does-not-exist")

        assert result.success is False
        assert "No preset 'does-not-exist'" in result.error
        assert "get_form_state" in result.error

    @pytest.mark.asyncio
    async def test_non_admin_without_assignment_cannot_see_preset(self, marketplace_file_repo, sdxl):
        db_repo = Mock()
        db_repo.get_available_preset_ids_for_user.return_value = []
        collaborators = make_collaborators(marketplace_file_repo, db_repo=db_repo)
        ctx = make_context(
            preset_collaborators=collaborators, is_admin=False, session_metadata={}
        )

        result = await self._tool().execute(ctx, preset_id=sdxl.id)

        assert result.success is False
        assert f"No preset '{sdxl.id}'" in result.error
        db_repo.get_available_preset_ids_for_user.assert_called_once_with("user-test")

    @pytest.mark.asyncio
    async def test_non_admin_with_assignment_can_see_preset(self, marketplace_file_repo, sdxl):
        db_repo = Mock()
        db_repo.get_available_preset_ids_for_user.return_value = [sdxl.id]
        collaborators = make_collaborators(marketplace_file_repo, db_repo=db_repo)
        ctx = make_context(
            preset_collaborators=collaborators, is_admin=False, session_metadata={}
        )

        result = await self._tool().execute(ctx, preset_id=sdxl.id)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_admin_bypasses_assignment_check(self, marketplace_file_repo, sdxl):
        db_repo = Mock()
        db_repo.get_available_preset_ids_for_user.side_effect = AssertionError("must not be called for admins")
        collaborators = make_collaborators(marketplace_file_repo, db_repo=db_repo)
        ctx = make_context(preset_collaborators=collaborators, is_admin=True, session_metadata={})

        result = await self._tool().execute(ctx, preset_id=sdxl.id)

        assert result.success is True


def _field(name, type_="string", label=None, default=None, ai_hint=None, configuration=None):
    return FieldTemplate(
        type=type_, name=name, label=label, default=default, ai_hint=ai_hint, configuration=configuration
    )


def _synthetic_preset(preset_id, modes, llm=None):
    return PresetTemplate(
        id=preset_id,
        name=f"Preset {preset_id}",
        version="1.0.0",
        path=f"/presets/{preset_id}",
        description="A synthetic test preset.",
        modes=modes,
        llm=llm,
    )


def _synthetic_file_repo(*presets):
    by_id = {p.id: p for p in presets}
    repo = MagicMock()
    repo.find_preset_by_id.side_effect = lambda preset_id: by_id.get(preset_id)
    return repo


class TestGetPresetInfoModeAndGuideResolution:
    def _tool(self):
        return GetPresetInfoTool()

    def _preset_with_modes(self, preset_id, llm=None):
        modes = {
            "refs": ModeTemplate(forms=[FormTemplate(name="default", fields=[_field("prompt")], default=True)], pipes=[]),
            "video": ModeTemplate(forms=[FormTemplate(name="default", fields=[_field("prompt")], default=True)], pipes=[]),
        }
        return _synthetic_preset(preset_id, modes, llm=llm)

    @pytest.mark.asyncio
    async def test_form_state_mode_used_when_it_matches_the_resolved_preset(self):
        preset = self._preset_with_modes("p1", llm={
            "guide": "Base guide.",
            "modes": {"video": {"guide": "Video guide."}},
        })
        collaborators = make_collaborators(_synthetic_file_repo(preset))
        ctx = make_context(
            preset_collaborators=collaborators,
            is_admin=True,
            session_metadata={"form_state": {"preset": "p1", "mode": "video"}},
        )

        result = await self._tool().execute(ctx)

        data = json.loads(result.data)
        assert data["mode"] == "video"
        assert data["llm_guide"] == "Video guide."

    @pytest.mark.asyncio
    async def test_explicit_preset_id_does_not_borrow_another_presets_mode(self):
        active_preset = self._preset_with_modes("p1")
        other_preset = self._preset_with_modes("p-other", llm={
            "guide": "Other base guide.",
            "modes": {"video": {"guide": "Should not apply - video is not p-other's default mode."}},
        })
        collaborators = make_collaborators(_synthetic_file_repo(active_preset, other_preset))
        ctx = make_context(
            preset_collaborators=collaborators,
            is_admin=True,
            session_metadata={"form_state": {"preset": "p1", "mode": "video"}},
        )

        result = await self._tool().execute(ctx, preset_id="p-other")

        data = json.loads(result.data)
        assert data["mode"] == "refs"
        assert data["llm_guide"] == "Other base guide."
        assert data["llm_guide_modes"] == ["video"]

    @pytest.mark.asyncio
    async def test_no_form_state_falls_back_to_default_mode(self):
        preset = self._preset_with_modes("p1", llm={"guide": "Base guide."})
        collaborators = make_collaborators(_synthetic_file_repo(preset))
        ctx = make_context(preset_collaborators=collaborators, is_admin=True, session_metadata={})

        result = await self._tool().execute(ctx, preset_id="p1")

        data = json.loads(result.data)
        assert data["mode"] == "refs"
        assert data["llm_guide"] == "Base guide."
        assert "llm_guide_modes" not in data


class TestGetPresetInfoResolutionOrder:
    def _tool(self):
        return GetPresetInfoTool()

    @pytest.mark.asyncio
    async def test_no_arg_resolves_via_form_state(self, marketplace_file_repo, sdxl):
        collaborators = make_collaborators(marketplace_file_repo)
        ctx = make_context(
            preset_collaborators=collaborators,
            is_admin=True,
            session_metadata={"form_state": {"preset": sdxl.id, "mode": "txt2img"}},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        assert json.loads(result.data)["id"] == sdxl.id

    @pytest.mark.asyncio
    async def test_falls_back_to_legacy_session_metadata_preset_id(self, marketplace_file_repo, sdxl):
        collaborators = make_collaborators(marketplace_file_repo)
        ctx = make_context(
            preset_collaborators=collaborators,
            is_admin=True,
            session_metadata={"preset_id": sdxl.id},
        )

        result = await self._tool().execute(ctx)

        assert result.success is True
        assert json.loads(result.data)["id"] == sdxl.id

    @pytest.mark.asyncio
    async def test_explicit_preset_id_wins_over_both_session_keys(self, marketplace_file_repo, sdxl):
        collaborators = make_collaborators(marketplace_file_repo)
        ctx = make_context(
            preset_collaborators=collaborators,
            is_admin=True,
            session_metadata={
                "form_state": {"preset": "does-not-exist"},
                "preset_id": "also-does-not-exist",
            },
        )

        result = await self._tool().execute(ctx, preset_id=sdxl.id)

        assert result.success is True
        assert json.loads(result.data)["id"] == sdxl.id

    @pytest.mark.asyncio
    async def test_neither_present_gives_human_error(self):
        ctx = make_context(preset_collaborators=Mock(), session_metadata={})

        result = await self._tool().execute(ctx)

        assert result.success is False
        assert "No preset_id" in result.error
        assert "session metadata" in result.error
