from types import SimpleNamespace

from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.presets import operations
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.schema import validate_manifest
from tests.features.editor_tasks.support import CROP, load, write_preset

PRESET_ID = "01TESTMODESUNVALIDATED00"


def manifest(extra=""):
    return (
        f'schema: 1\nid: "{PRESET_ID}"\nname: "Flag"\nversion: "1.0.0"\n'
        f'category: "image"\nengine: "native"\nmodes:\n  - txt2img\n  - inpaint\n{extra}'
    )


def write(root, extra=""):
    preset = root / "Flag"
    preset.mkdir(parents=True)
    (preset / "preset.yml").write_text(manifest(extra), encoding="utf-8")
    (preset / "tests.yml").write_text("schema: 1\ncases: []\n", encoding="utf-8")
    for mode in ("txt2img", "inpaint"):
        mode_dir = preset / "modes" / mode
        mode_dir.mkdir(parents=True)
        (mode_dir / "form.yml").write_text('name: "custom"\nfields: []\n', encoding="utf-8")
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n", encoding="utf-8")
    return preset


def served(root):
    loader = PresetTemplateLoader([str(root)])
    collaborators = SimpleNamespace(file_repo=FilePresetRepository(loader), requirements_cache=None)
    return collaborators, operations.get_available_modes(collaborators, PRESET_ID)


def lint_errors(root):
    return [i.message for i in PresetLinter([str(root)]).lint() if i.level == "error" and "modes_unvalidated" in i.message]


def test_schema_accepts_the_list_and_defaults_to_empty():
    data = {
        "schema": 1, "id": PRESET_ID, "name": "Flag", "version": "1.0.0", "category": "image",
        "engine": "native", "modes": ["txt2img", "inpaint"],
    }
    parsed, problems = validate_manifest(data)
    assert problems == [] and parsed.modes_unvalidated == []
    parsed, problems = validate_manifest({**data, "modes_unvalidated": ["inpaint"]})
    assert problems == [] and parsed.modes_unvalidated == ["inpaint"]


def test_schema_rejects_a_non_list():
    data = {
        "schema": 1, "id": PRESET_ID, "name": "Flag", "version": "1.0.0", "category": "image",
        "engine": "native", "modes": ["txt2img"], "modes_unvalidated": "txt2img",
    }
    parsed, problems = validate_manifest(data)
    assert parsed is None and problems


def test_modes_endpoint_marks_only_the_listed_modes(tmp_path):
    write(tmp_path, "modes_unvalidated:\n  - inpaint\n")
    collaborators, result = served(tmp_path)
    flags = {m["name"]: m["unvalidated"] for m in result["modes"]}
    assert flags == {"txt2img": False, "inpaint": True}
    info = collaborators.file_repo.preset_to_info(collaborators.file_repo.find_preset_by_id(PRESET_ID))
    assert info.modes_unvalidated == ["inpaint"]


def test_absent_key_means_every_mode_validated(tmp_path):
    write(tmp_path)
    _, result = served(tmp_path)
    assert [m["unvalidated"] for m in result["modes"]] == [False, False]


def test_lint_flags_an_id_that_is_not_a_mode(tmp_path):
    write(tmp_path, "modes_unvalidated:\n  - ghost\n")
    assert lint_errors(tmp_path) == ["modes_unvalidated: 'ghost' is not listed in modes:"]


def test_lint_accepts_a_listed_mode(tmp_path):
    write(tmp_path, "modes_unvalidated:\n  - inpaint\n")
    assert lint_errors(tmp_path) == []


def test_editor_offer_inherits_the_flag_from_its_source_mode(tmp_path):
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, CROP, preset_id="A-CROP")
    loader = load(root)
    template = next(t for t in loader.all() if t.id == "A-CROP")
    manager = EditorTaskManager(register_core_tasks(TaskRegistry()), lambda: loader.all(), lambda user: {"A-CROP"}, lambda engine: True)
    user = SimpleNamespace(id="u1")

    def flag():
        offers = next(e for e in manager.offers(user) if e["id"] == "edit_region")["offers"]
        return offers[0]["unvalidated"]

    assert flag() is False
    template.modes_unvalidated = ["edit"]
    assert flag() is True
