from unittest.mock import Mock

import pytest

from src.features.formulas import operations
from src.features.formulas.collaborators import FormulaCollaborators
from src.features.formulas.dto import CreateFormulaRequest, PlanFormulaRequest
from src.features.formulas.errors import FormulaError
from src.features.formulas.repository import FormulaRepository
from src.features.formulas.sources import PresetFormSource
from src.features.presets import PresetTemplateLoader
from src.features.presets.collaborators import PresetCollaborators
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.formula_groups import resolve_formula_groups
from src.platform.templating import TemplateProcessor
from tests.features.formulas.conftest import FakeModels, make_user


@pytest.fixture(scope="module")
def preset_world():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.get_all_presets()
    preset = next(item for item in loader.presets if str(item.path).rstrip("/").endswith("MiniMax-H3"))
    db_repo = Mock()
    db_repo.get_preset_form_overrides.return_value = {}
    preset_collaborators = PresetCollaborators(
        preset_loader=loader,
        preset_processor=Mock(),
        template_processor=TemplateProcessor(Mock()),
        file_repo=FilePresetRepository(loader),
        db_repo=db_repo,
        user_repo=Mock(),
        group_repo=Mock(),
        pipeline_builder=Mock(),
        pipe_catalog=Mock(),
        plugins=Mock(),
        settings=Mock(),
    )
    return preset, PresetFormSource(preset_collaborators)


@pytest.fixture
def real(preset_world, connection):
    preset, source = preset_world
    models = FakeModels()
    collaborators = FormulaCollaborators(repository=FormulaRepository(), forms=source, models=models)
    return preset, source.load(preset.id, "video", None), collaborators, models


def option_values(spec):
    return [item["value"] for item in spec["options"]]


def save(collaborators, preset, groups, values, name="Real"):
    return operations.create_formula(
        collaborators,
        "user-1",
        CreateFormulaRequest(
            preset_id=preset.id, mode="video", name=name, groups=[{"id": group} for group in groups], values=values
        ),
    )


def test_create_keeps_only_declared_fields_and_stores_the_resolvers_groups(real):
    preset, form, collaborators, _ = real
    profile = option_values(form.fields["speed_profile"])[0]

    formula = save(
        collaborators, preset, ["speed", "size"],
        {
            "speed_profile": profile, "steps": 4, "resolution": option_values(form.fields["resolution"])[0],
            "prompt": "a cat", "negative_prompt": "bad", "seed": 5, "quantity": 2, "image": "x.png",
            "model": "model:abc",
        },
    )

    resolved = {group.id: group for group in resolve_formula_groups(preset, "video", None)}
    assert set(formula.values) == {"speed_profile", "steps", "resolution"}
    assert [group["id"] for group in formula.groups] == ["speed", "size"]
    for stored in formula.groups:
        assert stored["label"] == resolved[stored["id"]].label
        assert set(stored["fields"]) <= set(resolved[stored["id"]].fields)
    assert formula.signatures["steps"] == {"type": "slider", "min": 2, "max": 100, "step": 1}


def test_a_group_the_mode_does_not_declare_is_refused(real):
    preset, _, collaborators, _ = real

    with pytest.raises(FormulaError) as caught:
        save(collaborators, preset, ["nonexistent"], {"steps": 4})

    assert caught.value.code == "unknown_group"


def test_plan_reports_changes_and_matches_against_current_values(real):
    preset, form, collaborators, _ = real
    profiles = option_values(form.fields["speed_profile"])
    formula = save(collaborators, preset, ["speed"], {"speed_profile": profiles[0], "steps": 4})

    result = operations.plan_formula(
        collaborators, make_user(), formula.id,
        PlanFormulaRequest(current_values={"speed_profile": profiles[1], "steps": 4, "prompt": "keep"}),
    )

    assert [item["name"] for item in result["changes"]] == ["speed_profile"]
    assert result["changes"][0]["old"] == profiles[1] and result["changes"][0]["new"] == profiles[0]
    assert [item["name"] for item in result["same"]] == ["steps"]
    assert result["skips"] == []
    assert next(item for item in result["same"] if item["name"] == "steps")["group_id"] == "speed"


def test_a_stored_step_count_outside_the_live_range_is_skipped_not_clamped(real):
    preset, form, collaborators, _ = real
    formula = save(collaborators, preset, ["speed"], {"speed_profile": option_values(form.fields["speed_profile"])[0], "steps": 4})
    stored = collaborators.repository.get_for_owner("user-1", formula.id)
    stored.values["steps"] = 500
    collaborators.repository.update(stored)

    result = operations.plan_formula(collaborators, make_user(), formula.id, PlanFormulaRequest(current_values={"steps": 20}))

    assert [(item["name"], item["code"]) for item in result["skips"]] == [("steps", "out_of_range")]
    assert result["skips"][0]["detail"] == {"min": 2, "max": 100, "step": 1}
    assert "steps" not in [item["name"] for item in result["changes"]]


def test_loras_resolve_against_the_real_picker_configuration(real):
    preset, _, collaborators, models = real
    models.add("l1", model_type="lora")
    formula = save(collaborators, preset, ["loras"], {"loras": [{"model": "model:l1", "strength": 0.75}, {"model": "model:gone", "strength": 1.0}]})

    result = operations.plan_formula(collaborators, make_user(), formula.id, PlanFormulaRequest(current_values={}))

    assert result["changes"][0]["new"][0]["model"] == "model:l1"
    assert [(item["code"], item["detail"]) for item in result["skips"]] == [("lora_unavailable", {"model": "model:gone"})]
