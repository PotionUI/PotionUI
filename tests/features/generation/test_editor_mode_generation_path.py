import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from scripts import preset_render
from src.features.elixirs.generation_port import GenerationPort
from src.features.forms.binding import bind_form
from src.features.generation.dto import GenerationRequest
from src.features.generation.exceptions import ModeIsEditorError
from src.features.generation.orchestrator import GenerationOrchestrator
from src.features.generation.routes import GenerationController
from src.features.llm.tools.base import ToolContext
from src.features.llm.tools.builtin.run_generation_tool import RunGenerationTool
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.presets import operations
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.editor_modes import editor_overrides, form_overrides_for, input_field_names
from src.features.presets.extensions import walk_fields
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.templates import find_mode
from src.platform.security.user import AccountType
from src.platform.templating import TemplateProcessor
from tests.features.presets.editor_mode_trees import (
    KREA2,
    KREA2_ID,
    KREA_EDITOR,
    SDXL,
    SDXL_INPAINT_EDITOR,
    copy_preset,
    write_editor_mode,
)

EDITOR_KEY = "editor:edit"
SPEC = {**KREA_EDITOR, "fixed": {**KREA_EDITOR["fixed"], "fit_mode": "crop"}}


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "presets"
    krea = copy_preset(KREA2, root)
    sdxl = copy_preset(SDXL, root)
    write_editor_mode(krea, "edit", SPEC)
    write_editor_mode(sdxl, "inpaint", SDXL_INPAINT_EDITOR)
    loader = PresetTemplateLoader([str(root)])
    loader.load_presets()
    assert loader.load_errors == {}
    return SimpleNamespace(loader=loader, krea=loader.get(KREA2_ID), stored={})


def collaborators(world, stored=None):
    db_repo = Mock()
    db_repo.get_preset_form_overrides.return_value = stored if stored is not None else world.stored
    return PresetCollaborators(
        preset_loader=world.loader,
        preset_processor=Mock(),
        template_processor=TemplateProcessor(Mock()),
        file_repo=FilePresetRepository(world.loader),
        db_repo=db_repo,
        user_repo=Mock(),
        group_repo=Mock(),
        pipeline_builder=Mock(),
        pipe_catalog=Mock(),
        plugins=Mock(),
        settings=Mock(),
    )


def names(fields):
    return [f.name for f, _ in walk_fields(fields) if f.name]


def served_fields(schema):
    found = {}

    def walk(node):
        for child in node.get("children") or []:
            walk(child)
        if node.get("name"):
            found[node["name"]] = node

    for node in schema["form_schema"]["properties"].values():
        walk(node)
    return found


def served_names(schema):
    return set(served_fields(schema))


def test_a_generation_with_an_editor_key_binds_renders_and_builds_pipes(world):
    processor = preset_render.build_processor()
    form = preset_render.build_fixture_form_data(world.krea, EDITOR_KEY)
    assert form["fit_mode"] == "crop"
    record = preset_render.render_preset_mode(processor, world.krea, EDITOR_KEY, form)
    assert "error" not in record, record
    generator = next(pipe for pipe in record["pipes"] if pipe["name"] == "generator/krea2_edit")
    assert generator["config"]["fit_mode"]["value"] == "crop"
    plain = preset_render.render_preset_mode(processor, world.krea, "edit")
    plain_generator = next(pipe for pipe in plain["pipes"] if pipe["name"] == "generator/krea2_edit")
    assert plain_generator["config"]["fit_mode"]["value"] == "fit"
    assert [p["name"] for p in record["pipes"]] == [p["name"] for p in plain["pipes"]]


def test_a_plain_mode_renders_exactly_as_before_through_the_converted_lookups(world):
    processor = preset_render.build_processor()
    first = preset_render.render_preset_mode(processor, world.krea, "edit")
    assert "error" not in first
    assert first["mode"] == "edit"
    assert preset_render.golden_filename(world.krea, "edit") == f"{KREA2_ID}__edit.json"
    assert preset_render.golden_filename(world.krea, EDITOR_KEY) == f"{KREA2_ID}__editor-edit.json"


def test_the_golden_run_writes_an_editor_golden_beside_the_generation_ones(world, tmp_path):
    out = tmp_path / "golden"
    result = preset_render.run_golden_all(out, presets_root=tmp_path / "presets", forms_dir=tmp_path / "forms")
    written = {path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for path in result["written"]}
    assert f"{KREA2_ID}__editor-edit.json" in written
    assert f"{KREA2_ID}__edit.json" in written
    assert not any(":" in name for name in written)
    assert f"{KREA2_ID}__editor-edit.json" in {path.name for path in (tmp_path / "forms").glob("*.json")}


def test_the_served_form_omits_exactly_inputs_fixed_and_hidden_and_keeps_change_and_add(world):
    schema = operations.get_form_schema(collaborators(world), KREA2_ID, mode=EDITOR_KEY)
    editor = find_mode(world.krea, EDITOR_KEY)
    generation = operations.get_form_schema(collaborators(world), KREA2_ID, mode="edit")
    owned = set(editor.editor.fixed) | set(editor.editor.hide) | set(input_field_names(editor.editor))
    assert served_names(schema) == (served_names(generation) - owned) | {"region_context"}
    assert owned & served_names(generation)
    assert not owned & served_names(schema)
    assert served_fields(schema)["resolution"]["title"] == "Detail"
    assert served_fields(generation)["resolution"]["title"] != "Detail"
    assert "region_context" not in served_names(generation)


def test_the_form_route_controller_serves_the_editor_form(world):
    from src.features.presets.routes import PresetController

    controller = PresetController(collaborators(world), Mock())
    response = asyncio.run(controller.get_form_schema(KREA2_ID, EDITOR_KEY, None))
    assert "region_context" in served_names(response.data)
    assert "fit_mode" not in served_names(response.data)


def test_an_unknown_editor_key_is_a_missing_mode_for_the_form(world):
    from src.features.presets.exceptions import ModeNotFoundException

    with pytest.raises(ModeNotFoundException):
        operations.get_form_schema(collaborators(world), KREA2_ID, mode="editor:nope")


def test_binding_pins_fixed_and_hidden_values_over_an_admin_override_of_the_from_mode(world):
    stored = {
        "edit": {
            "fit_mode": {"default": "fit", "editable": True},
            "ref_position_a": {"default": "beside"},
            "cfg": {"default": 2.0, "editable": False},
        }
    }
    overrides = form_overrides_for(world.krea, EDITOR_KEY, stored)
    assert overrides["fit_mode"] == {"default": "crop", "visible": False}
    assert overrides["ref_position_a"] == {"visible": False}
    assert overrides["cfg"] == {"default": 2.0, "editable": False}
    assert "source_image" not in overrides

    from src.features.presets.fixture_form import build_default_form_data

    form, _ = build_default_form_data(world.krea, EDITOR_KEY, "t")
    form.update({"quantity": 1, "seed": 1, "fit_mode": "fit", "ref_position_a": "beside", "cfg": 9.0})
    bound = bind_form(world.krea, EDITOR_KEY, None, form, None, storage_dir=None, field_overrides=overrides)
    assert bound.values["fit_mode"] == "crop"
    assert bound.values["ref_position_a"] == "aligned"
    assert bound.values["cfg"] == 2.0
    assert bound.values["source_image"] == form["source_image"]


def test_a_plain_key_overrides_are_the_stored_dict_untouched(world):
    stored = {"edit": {"cfg": {"default": 2.0}}}
    assert form_overrides_for(world.krea, "edit", stored) is stored["edit"]
    assert form_overrides_for(world.krea, "txt2img", stored) == {}
    assert form_overrides_for(world.krea, "edit", None) == {}


def test_an_editor_mode_inherits_the_admin_overrides_of_its_from_mode_for_fields_it_does_not_own(world):
    stored = {"edit": {"cfg": {"default": 2.0, "editable": False}}}
    served = operations.get_form_schema(collaborators(world, stored), KREA2_ID, mode=EDITOR_KEY)
    assert served_fields(served)["cfg"]["readonly"] is True


def test_an_editor_run_sets_its_flag_in_process_and_a_body_cannot(world):
    body = GenerationRequest.model_validate(
        {"preset_id": KREA2_ID, "mode": EDITOR_KEY, "_editor_run": True, "editor_run": True}
    )
    assert body._editor_run is False
    with pytest.raises(ModeIsEditorError):
        GenerationOrchestrator._refuse_editor_mode(body)
    body._editor_run = True
    assert GenerationOrchestrator._refuse_editor_mode(body) is None


@pytest.mark.parametrize("mode", ["txt2img", "edit", None, ""])
def test_a_plain_key_passes_the_guard(mode):
    assert GenerationOrchestrator._refuse_editor_mode(SimpleNamespace(mode=mode)) is None
    assert GenerationOrchestrator._refuse_editor_mode(Mock()) is None


def guarded_orchestrator():
    orchestrator = object.__new__(GenerationOrchestrator)
    orchestrator.status_tracker = Mock()
    return orchestrator


def editor_request():
    return GenerationRequest(preset_id=KREA2_ID, mode=EDITOR_KEY, form_data={})


def user(account_type=AccountType.USER):
    return SimpleNamespace(id="u1", account_type=account_type)


def assert_refusal(error: HTTPException):
    assert error.status_code == 422
    assert error.detail["error"] == "mode_is_editor"
    assert "editor" in error.detail["message"].lower()
    assert KREA2_ID not in error.detail["message"]


@pytest.mark.asyncio
async def test_the_orchestrator_refuses_an_editor_key_on_start_and_on_memory_preview():
    orchestrator = guarded_orchestrator()
    with pytest.raises(ModeIsEditorError):
        await orchestrator.start_generation(editor_request(), "u1")
    with pytest.raises(ModeIsEditorError):
        await orchestrator.preview_memory(editor_request(), "u1")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start_generation", "preview_memory"])
@pytest.mark.parametrize("account_type", [AccountType.USER, AccountType.ADMIN])
async def test_the_http_controller_answers_422_mode_is_editor(action, account_type):
    orchestrator = guarded_orchestrator()
    orchestrator._queue_dispatcher = Mock()
    controller = GenerationController(orchestrator, Mock(), Mock(), Mock(), output_broadcaster=Mock())
    with pytest.raises(HTTPException) as caught:
        await getattr(controller, action)(editor_request(), user(account_type))
    assert_refusal(caught.value)


@pytest.mark.asyncio
async def test_the_elixir_port_and_the_plugin_api_refuse_an_editor_key(monkeypatch):
    orchestrator = guarded_orchestrator()
    port = GenerationPort(*([Mock()] * 11))
    port.orchestrator = orchestrator
    with pytest.raises(ModeIsEditorError):
        await port.submit(user(), {"preset_id": KREA2_ID, "mode": EDITOR_KEY, "form_data": {}})

    from src.plugin_api import generation as plugin_generation

    monkeypatch.setattr(
        plugin_generation, "get_container", lambda: SimpleNamespace(generation_orchestrator=orchestrator)
    )
    monkeypatch.setattr(plugin_generation, "_require_available_preset", lambda *args: None)
    with pytest.raises(ModeIsEditorError):
        await plugin_generation.submit_generation(user(), editor_request())


@pytest.mark.asyncio
async def test_the_chat_start_generation_tool_returns_a_plain_message():
    context = ToolContext(user_id="u1", generation_orchestrator=guarded_orchestrator())
    result = await StartGenerationTool()._submit(context, editor_request())
    assert result.success is False
    assert "image editor" in result.error
    assert "Traceback" not in result.error


@pytest.mark.asyncio
async def test_the_chat_run_generation_tool_returns_a_plain_message():
    context = ToolContext(
        user_id="u1",
        generation_orchestrator=guarded_orchestrator(),
        session_metadata={"form_state": {"preset": KREA2_ID, "mode": EDITOR_KEY, "form_data": {}}},
    )
    result = await RunGenerationTool().execute_confirmed(context)
    assert result.success is False
    assert "image editor" in result.error


def test_the_sessions_and_grid_routes_start_through_the_controller_that_refuses():
    import inspect

    from src.features.generation.grids import routes as grid_routes
    from src.features.sessions import routes as session_routes

    assert "start_generation(" in inspect.getsource(grid_routes)
    assert "generation_controller_for(container).start_generation(" in inspect.getsource(session_routes)


def test_an_sdxl_inpaint_editor_mode_serves_without_its_picture_and_mask_inputs(world):
    sdxl_id = next(p.id for p in world.loader.presets if "inpaint" in p.modes)
    own = served_names(operations.get_form_schema(collaborators(world), sdxl_id, mode="inpaint"))
    schema = operations.get_form_schema(collaborators(world), sdxl_id, mode="editor:inpaint")
    served = served_names(schema)
    assert "source_image" in own
    assert not {"source_image", "source_image_inpaint_mask"} & served
    assert served == own - {"source_image", "source_image_inpaint_mask", "quantity"}


def test_the_model_access_check_sees_the_model_fields_of_an_editor_mode(world):
    bound = SimpleNamespace(values={"diffusion_model": "krea2_turbo_bf16.safetensors", "seed": 1})
    plain = GenerationOrchestrator._unresolved_slot_files(bound, set(), world.krea, "edit")
    editor = GenerationOrchestrator._unresolved_slot_files(bound, set(), world.krea, EDITOR_KEY)
    assert plain and editor == plain
    assert GenerationOrchestrator._unresolved_slot_files(bound, set(), world.krea, "editor:nope") == []


def test_the_served_editor_form_carries_the_prompt_resources_and_syntax_of_its_from_mode(world):
    resources = [{"field": "source_image_b", "kind": "image", "token": "pic"}]
    world.krea.prompt_resources = {"edit": resources}
    world.krea.prompt_syntax = {"edit": [{"syntax": "weights"}]}

    editor = operations.get_form_schema(collaborators(world), KREA2_ID, mode=EDITOR_KEY)
    plain = operations.get_form_schema(collaborators(world), KREA2_ID, mode="edit")

    assert [item["field"] for item in editor["prompt_resources"]] == ["source_image_b"]
    assert editor["prompt_syntax"] == plain["prompt_syntax"] == [{"syntax": "weights"}]


def test_the_formula_source_names_the_from_mode_of_an_editor_key(world):
    from src.features.formulas.sources import PresetFormSource

    source = PresetFormSource(collaborators(world))

    assert source.from_mode_of(KREA2_ID, EDITOR_KEY) == "edit"
    assert source.from_mode_of(KREA2_ID, "edit") is None
    assert source.from_mode_of(KREA2_ID, "editor:nope") is None
