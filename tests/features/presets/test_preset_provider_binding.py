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
