from pathlib import Path

import pytest

from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.schema import validate_manifest


def manifest_data(**overrides):
    data = {
        "schema": 1,
        "id": "01K0W24A3RADXXABH16YQ7KE90",
        "name": "Bound",
        "version": "1.0.0",
        "category": "image",
        "engine": "cloud",
        "driver": "cloud.fake",
        "modes": ["txt2img"],
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def write_preset(root: Path, name: str, preset_id: str, engine: str, driver):
    preset_dir = root / name
    (preset_dir / "modes" / "txt2img").mkdir(parents=True)
    lines = [
        "schema: 1",
        f'id: "{preset_id}"',
        'name: "Bound"',
        'version: "1.0.0"',
        'category: "image"',
        f'engine: "{engine}"',
        *([f'driver: "{driver}"'] if driver else []),
        "modes:",
        "  - txt2img",
    ]
    (preset_dir / "preset.yml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text("pipeline: []\n", encoding="utf-8")
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n", encoding="utf-8")
    return preset_dir


def test_a_cloud_preset_with_a_driver_is_a_valid_manifest():
    manifest, errors = validate_manifest(manifest_data())

    assert errors == []
    assert manifest.driver == "cloud.fake"


def test_a_cloud_preset_must_name_its_driver():
    manifest, errors = validate_manifest(manifest_data(driver=None))

    assert manifest is None
    assert any("driver is required" in error for error in errors)


@pytest.mark.parametrize("driver", ["native.local", "fake", "Cloud.Fake", "cloud.", "cloud.fake.x"])
def test_a_cloud_driver_must_be_shaped_like_cloud_dot_name(driver):
    manifest, errors = validate_manifest(manifest_data(driver=driver))

    assert manifest is None
    assert any("driver" in error for error in errors)


def test_the_driver_must_start_with_the_presets_engine():
    manifest, errors = validate_manifest(manifest_data(engine="native", driver="cloud.fake"))

    assert manifest is None
    assert any("must start with the preset's engine ('native.')" in error for error in errors)


def test_a_native_preset_may_bind_to_one_of_its_own_drivers():
    manifest, errors = validate_manifest(manifest_data(engine="native", driver="native.local"))

    assert errors == [] and manifest.driver == "native.local"


def test_a_preset_without_a_driver_is_unchanged():
    manifest, errors = validate_manifest(manifest_data(engine="native", driver=None))

    assert errors == [] and manifest.driver is None


def test_the_loader_and_the_preset_info_carry_the_driver(tmp_path):
    write_preset(tmp_path, "Bound", "01BOUNDAAAAAAAAAAAAAAAAAAAA", "cloud", "cloud.fake")
    write_preset(tmp_path, "Plain", "01PLAINAAAAAAAAAAAAAAAAAAAA", "native", None)
    loader = PresetTemplateLoader([str(tmp_path)])

    assert loader.load_preset_by_id("01BOUNDAAAAAAAAAAAAAAAAAAAA").driver == "cloud.fake"
    assert loader.load_preset_by_id("01PLAINAAAAAAAAAAAAAAAAAAAA").driver is None
    by_id = {info["id"]: info for info in FilePresetRepository(loader).list_all_presets()}
    assert by_id["01BOUNDAAAAAAAAAAAAAAAAAAAA"]["driver"] == "cloud.fake"
    assert by_id["01PLAINAAAAAAAAAAAAAAAAAAAA"]["driver"] is None


def test_the_lint_warns_when_the_driver_is_not_registered(tmp_path):
    write_preset(tmp_path, "Bound", "01BOUNDBBBBBBBBBBBBBBBBBBBB", "cloud", "cloud.fake")

    issues = PresetLinter([str(tmp_path)], registered_drivers={"native.local"}).lint()

    warnings = [i for i in issues if i.level == "warning" and "cloud.fake" in i.message]
    assert len(warnings) == 1 and "not registered" in warnings[0].message
    assert not [i for i in issues if i.level == "error" and "driver" in i.message]


def test_the_lint_is_quiet_when_the_driver_is_registered(tmp_path):
    write_preset(tmp_path, "Bound", "01BOUNDCCCCCCCCCCCCCCCCCCCC", "cloud", "cloud.fake")

    issues = PresetLinter([str(tmp_path)], registered_drivers={"cloud.fake"}).lint()

    assert not [i for i in issues if "driver" in i.message]


def test_the_lint_does_not_check_registration_without_a_registry(tmp_path):
    write_preset(tmp_path, "Bound", "01BOUNDDDDDDDDDDDDDDDDDDDDD", "cloud", "cloud.fake")

    issues = PresetLinter([str(tmp_path)]).lint()

    assert not [i for i in issues if "not registered" in i.message]


def test_the_lint_reports_a_cloud_preset_without_a_driver_as_an_error(tmp_path):
    write_preset(tmp_path, "Bound", "01BOUNDEEEEEEEEEEEEEEEEEEEE", "cloud", None)

    issues = PresetLinter([str(tmp_path)], registered_drivers=set()).lint()

    assert any(i.level == "error" and "driver is required" in i.message for i in issues)


def test_a_preset_without_a_driver_gets_no_driver_finding_from_the_lint(tmp_path):
    write_preset(tmp_path, "Plain", "01PLAINBBBBBBBBBBBBBBBBBBBB", "native", None)

    assert PresetLinter([str(tmp_path)], registered_drivers=set()).lint() == []


def field_data(**overrides):
    data = {"type": "select", "name": "aspect_ratio", "capability": {"model_field": "model", "param": "aspect_ratio"}}
    data.update(overrides)
    return data


def test_a_field_can_bind_to_a_canonical_param():
    from src.features.presets.schema import validate_field_list

    fields, errors = validate_field_list([field_data()])

    assert errors == []
    assert fields[0].capability.param == "aspect_ratio"


def test_a_field_can_bind_to_a_provider_extra_or_a_media_role():
    from src.features.presets.schema import validate_field_list

    _, extra_errors = validate_field_list([field_data(capability={"model_field": "model", "param": "x.style"})])
    _, role_errors = validate_field_list([field_data(type="image", capability={"model_field": "model", "input": "first_frame"})])

    assert extra_errors == [] and role_errors == []


@pytest.mark.parametrize("capability", [
    {"model_field": "model"},
    {"model_field": "model", "param": "aspect_ratio", "input": "reference"},
    {"model_field": "model", "param": "not_a_param"},
    {"model_field": "model", "input": "not_a_role"},
    {"model_field": " ", "param": "aspect_ratio"},
    {"param": "aspect_ratio"},
    {"model_field": "model", "param": "aspect_ratio", "extra": 1},
])
def test_a_malformed_capability_is_a_schema_error(capability):
    from src.features.presets.schema import validate_field_list

    fields, errors = validate_field_list([field_data(capability=capability)])

    assert fields is None and errors


def test_a_provider_options_field_names_only_its_model_field():
    from src.features.presets.schema import validate_field_list

    _, good = validate_field_list([{"type": "cloud_options", "name": "opts", "capability": {"model_field": "model"}}])
    _, bad = validate_field_list([{"type": "cloud_options", "name": "opts", "capability": {"model_field": "model", "param": "quality"}}])

    assert good == [] and bad


def test_the_form_schema_the_frontend_receives_carries_the_binding():
    from src.features.fields.cloud_options import CloudOptions
    from src.features.fields.slider import Slider
    from src.features.presets.templates import FieldTemplate

    bound = FieldTemplate(type="slider", name="quality", capability={"model_field": "model", "param": "quality", "input": None})
    plain = FieldTemplate(type="slider", name="steps")
    options = FieldTemplate(type="cloud_options", name="opts", capability={"model_field": "model"})

    assert Slider(None).output(bound)["capability"] == {"model_field": "model", "param": "quality"}
    assert "capability" not in Slider(None).output(plain)
    schema = CloudOptions(None).output(options)
    assert schema["capability"] == {"model_field": "model"}
    assert schema["configuration"] == {"include_unbound": True}


def test_the_provider_options_field_documents_its_config_key_for_the_lint():
    from src.features.fields.cloud_options import CloudOptions

    assert [spec.name for spec in CloudOptions.configuration()] == ["include_unbound"]


def write_capability_form(root, form_lines):
    preset_dir = write_preset(root, "Capable", "01CAPABLEAAAAAAAAAAAAAAAAAA", "cloud", "cloud.fake")
    (preset_dir / "modes" / "txt2img" / "form.yml").write_text("\n".join(form_lines) + "\n", encoding="utf-8")
    return preset_dir


def test_a_preset_form_with_capability_bound_fields_lints_clean(tmp_path):
    write_capability_form(tmp_path, [
        "fields:",
        "  - {type: model, name: model, configuration: {model_type: cloud, tasks: [txt2img]}}",
        "  - {type: select, name: aspect_ratio, capability: {model_field: model, param: aspect_ratio}, configuration: {options: [{label: Square, value: '1:1'}]}}",
        "  - {type: image, name: references, capability: {model_field: model, input: reference}}",
        "  - {type: cloud_options, name: provider_options, capability: {model_field: model}, configuration: {include_unbound: false}}",
    ])

    issues = PresetLinter([str(tmp_path)], registered_drivers={"cloud.fake"}).lint()

    assert [i for i in issues if i.level == "error"] == []
    assert not [i for i in issues if "configuration has key" in i.message or "capability" in i.message]


def test_a_preset_form_with_a_bad_capability_fails_the_lint(tmp_path):
    write_capability_form(tmp_path, [
        "fields:",
        "  - {type: model, name: model}",
        "  - {type: slider, name: quality, capability: {model_field: model, param: nope}}",
    ])

    issues = PresetLinter([str(tmp_path)], registered_drivers={"cloud.fake"}).lint()

    assert any(i.level == "error" and "capability.param 'nope'" in i.message for i in issues)
