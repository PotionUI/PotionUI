import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.preset_info_tool import GetPresetInfoTool, ListPresetsTool
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _preset(preset_id, name, engine, modes, description="", tags=None):
    return PresetTemplate(
        id=preset_id, name=name, version="1.0.0", path=f"/presets/{preset_id}", description=description,
        engine=engine, tags=tags or [],
        modes={
            mode: ModeTemplate(
                forms=[FormTemplate(name="default", default=True, fields=[
                    FieldTemplate(type="textarea", name=f"{mode}_prompt", label="Prompt"),
                ])],
                pipes=[],
            )
            for mode in modes
        },
    )


@pytest.fixture
def collaborators():
    presets = {
        "flux/dev": _preset("flux/dev", "Flux Dev", "native", ["txt2img", "img2img"], "Fast photoreal", ["photo"]),
        "sdxl/base": _preset("sdxl/base", "SDXL", "native", ["t2i"], "Classic " + "x" * 300),
        "comfy/wan": _preset("comfy/wan", "Wan Video", "comfyui", ["txt2vid"], "Video"),
    }
    file_repo = Mock()
    file_repo.list_all_presets.side_effect = lambda: [
        {"id": p.id, "name": p.name, "engine": p.engine, "description": p.description, "tags": p.tags}
        for p in presets.values()
    ]
    file_repo.find_preset_by_id.side_effect = presets.get
    db_repo = Mock()
    db_repo.get_available_preset_ids_for_user.side_effect = (
        lambda user_id: {"user-1": ["flux/dev", "sdxl/base"]}.get(user_id, [])
    )
    return SimpleNamespace(file_repo=file_repo, db_repo=db_repo)


def _ctx(collaborators, user_id="user-1", **extra):
    return ToolContext(user_id=user_id, preset_collaborators=collaborators, chat_session=False, **extra)


@pytest.mark.asyncio
async def test_lists_only_the_presets_the_user_can_use_with_engine_and_modes(collaborators):
    payload = json.loads((await ListPresetsTool().execute(_ctx(collaborators))).data)

    assert [p["id"] for p in payload["presets"]] == ["flux/dev", "sdxl/base"]
    assert payload["presets"][0] == {
        "id": "flux/dev", "name": "Flux Dev", "engine": "native",
        "modes": ["txt2img", "img2img"], "description": "Fast photoreal", "tags": ["photo"],
    }
    assert len(payload["presets"][1]["description"]) <= 163
    assert payload["total"] == 2 and payload["has_more"] is False


@pytest.mark.asyncio
async def test_another_user_without_assignments_sees_none_and_is_told_why(collaborators):
    payload = json.loads((await ListPresetsTool().execute(_ctx(collaborators, user_id="user-2"))).data)

    assert payload["presets"] == []
    assert "admin" in payload["message"]


@pytest.mark.asyncio
async def test_query_engine_and_paging(collaborators):
    by_query = json.loads((await ListPresetsTool().execute(_ctx(collaborators), query="photo")).data)
    by_engine = json.loads((await ListPresetsTool().execute(_ctx(collaborators), engine="comfyui")).data)
    paged = json.loads((await ListPresetsTool().execute(_ctx(collaborators), limit=1)).data)

    assert [p["id"] for p in by_query["presets"]] == ["flux/dev"]
    assert by_engine["presets"] == [] and by_engine["message"] == "No presets matched."
    assert len(paged["presets"]) == 1 and paged["has_more"] is True


@pytest.mark.asyncio
async def test_get_preset_info_takes_an_explicit_mode_and_rejects_an_unknown_one(collaborators):
    picked = await GetPresetInfoTool().execute(_ctx(collaborators), preset_id="flux/dev", mode="img2img")
    unknown = await GetPresetInfoTool().execute(_ctx(collaborators), preset_id="flux/dev", mode="video")

    data = json.loads(picked.data)
    assert data["mode"] == "img2img"
    assert [f["name"] for f in data["form_fields"]] == ["img2img_prompt"]
    assert data["engine"] == "native"
    assert unknown.success is False
    assert "txt2img, img2img" in unknown.error


@pytest.mark.asyncio
async def test_get_preset_info_without_a_preset_id_points_at_list_presets(collaborators):
    result = await GetPresetInfoTool().execute(_ctx(collaborators))

    assert result.success is False
    assert "list_presets" in result.error


@pytest.mark.asyncio
async def test_start_generation_refuses_a_preset_the_user_cannot_use(collaborators):
    orchestrator = Mock()
    result = await StartGenerationTool().execute_confirmed(
        _ctx(collaborators, generation_orchestrator=orchestrator), preset_id="comfy/wan", prompt="a fox",
    )

    assert result.success is False
    assert "list_presets" in result.error
    orchestrator.start_generation.assert_not_called()


@pytest.mark.asyncio
async def test_start_generation_defaults_to_the_presets_first_mode_and_rejects_an_unknown_mode(collaborators):
    preview = await StartGenerationTool().execute(_ctx(collaborators), preset_id="sdxl/base", prompt="a fox")
    wrong = await StartGenerationTool().execute(_ctx(collaborators), preset_id="sdxl/base", mode="txt2img")

    assert json.loads(preview.data)["mode"] == "t2i"
    assert wrong.success is False
    assert "Its modes are: t2i" in wrong.error


@pytest.mark.asyncio
async def test_enhance_prompt_grounds_on_an_explicit_preset_only_when_the_user_can_use_it(collaborators, monkeypatch):
    from src.features.llm.tools.builtin.enhance_prompt_tool import EnhancePromptTool
    from src.features.prompt_enhancement import operations

    calls = []

    async def enhance(_collaborators, **kwargs):
        calls.append(kwargs)
        return {"candidates": [{"text": "a rich fox"}]}

    monkeypatch.setattr(operations, "enhance", enhance)
    context = _ctx(collaborators, prompt_enhancement_manager=object(), llm_id="llm-1")

    allowed = await EnhancePromptTool().execute(context, brief="a fox", preset_id="flux/dev")
    refused = await EnhancePromptTool().execute(context, brief="a fox", preset_id="comfy/wan")

    assert json.loads(allowed.data) == {"enhanced_prompt": "a rich fox"}
    assert calls[0]["form_state"] == {"form_data": {}, "preset": "flux/dev"}
    assert refused.success is False and "list_presets" in refused.error
    assert len(calls) == 1
