import pytest

from src.features.editor_tasks.declarations import (
    DECLARATION_FILE,
    DeclarationCache,
    owned_fields,
    parse_file,
    validate_mode,
)
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from tests.features.editor_tasks.support import (
    BASE_FIELDS,
    CROP,
    MASK,
    PLAIN_IMAGE,
    PRESET_ID,
    declaration,
    load,
    task,
    write_preset,
)

REGISTRY = register_core_tasks(TaskRegistry())


def need(task_id):
    found = REGISTRY.get(task_id)
    return found.prompt if found else None


def check(tmp_path, editor, fields=None, owner=None, promptless=(), mode="edit", raw=None):
    root = tmp_path / "presets"
    root.mkdir(exist_ok=True)
    write_preset(root, editor, fields=fields, mode=mode)
    template = load(root).get(PRESET_ID)
    parsed = parse_file(root / PRESET_ID / "modes" / mode / DECLARATION_FILE)
    return validate_mode(PRESET_ID, mode, template.modes[mode], parsed, need, owner, promptless), template


def test_a_valid_crop_declaration_is_accepted(tmp_path):
    result, _ = check(tmp_path, CROP)

    assert result.errors == []
    assert [item.task for item in result.tasks] == ["edit_region"]
    assert result.tasks[0].size.area == 1048576
    assert result.tasks[0].size.multiple == 16
    assert result.tasks[0].context == 0.5


def test_a_valid_mask_declaration_is_accepted(tmp_path):
    result, _ = check(tmp_path, MASK)

    assert result.errors == []
    assert result.tasks[0].inputs.mask == "source_image_inpaint_mask"


def test_a_media_list_input_with_an_index_is_accepted(tmp_path):
    result, _ = check(tmp_path, declaration(task(inputs={"image": {"field": "references", "index": 0}})))

    assert result.errors == []


def test_no_file_means_no_offers(tmp_path):
    result, _ = check(tmp_path, None)

    assert result.present is False
    assert result.tasks == []
    assert result.errors == []


def test_an_empty_file_is_a_schema_error(tmp_path):
    result, _ = check(tmp_path, "")

    assert result.tasks == []
    assert "schema_version" in result.errors[0]


@pytest.mark.parametrize("version", [2, 0, "1"])
def test_an_unknown_or_wrong_schema_version_is_an_error(tmp_path, version):
    result, _ = check(tmp_path, {**CROP, "schema_version": version})

    assert result.tasks == []
    assert "schema_version" in result.errors[0]


def test_a_missing_schema_version_is_an_error(tmp_path):
    result, _ = check(tmp_path, {"tasks": CROP["tasks"]})

    assert result.tasks == []
    assert "schema_version" in result.errors[0]


def test_an_unknown_key_is_an_error(tmp_path):
    result, _ = check(tmp_path, declaration(task(colour="red")))

    assert result.tasks == []
    assert "colour" in result.errors[0]


def test_the_same_task_twice_is_an_error(tmp_path):
    result, _ = check(tmp_path, declaration(task(), task()))

    assert result.tasks == []
    assert "declared twice" in result.errors[0]


def test_an_unknown_core_task_is_an_error(tmp_path):
    result, _ = check(tmp_path, declaration(task(task="paint_it_red")))

    assert result.tasks == []
    assert "unknown task 'paint_it_red'" in result.errors[0]


def test_a_plugin_task_that_is_not_registered_is_a_warning_and_is_skipped(tmp_path):
    result, _ = check(tmp_path, declaration(task(task="shiny.colorize"), task()))

    assert result.errors == []
    assert [item.task for item in result.tasks] == ["edit_region"]
    assert "shiny.colorize" in result.warnings[0]


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"inputs": {"image": "nope"}}, "not a field of the mode"),
        ({"inputs": {"image": "seed"}}, "not an image field"),
        ({"inputs": {"image": {"field": "source_image", "index": 0}}}, "needs a media field"),
        ({"region": "mask", "inputs": {"image": "source_image"}}, "needs inputs.mask"),
        ({"region": "mask", "inputs": {"image": "source_image", "mask": "other_mask"}}, "must be 'source_image_inpaint_mask'"),
        ({"size": {"field": "seed"}}, "not a resolution field"),
        ({"size": {"field": "nope"}}, "not a field of the mode"),
        ({"size": {"multiple": 24}}, "multiple"),
        ({"size": {"area": 1000}}, "area"),
        ({"size": {"area": 9000000}}, "area"),
        ({"set": {"nope": 1}}, "not a field of the mode"),
        ({"set": {"output_size": "huge"}}, "not an option"),
        ({"set": {"match_source": "yes"}}, "true or false"),
        ({"seed_field": "nope"}, "seed_field"),
        ({"quantity_field": "nope"}, "quantity_field"),
        ({"owned": ["nope"]}, "owned"),
    ],
)
def test_each_invalid_case_drops_the_task(tmp_path, changes, message):
    result, _ = check(tmp_path, declaration(task(**changes)))

    assert result.tasks == []
    assert any(message in error for error in result.errors), result.errors


def test_a_mask_input_needs_allow_inpaint_on_the_image_field(tmp_path):
    fields = [PLAIN_IMAGE, *BASE_FIELDS[1:]]

    result, _ = check(tmp_path, MASK, fields=fields)

    assert result.tasks == []
    assert "allow_inpaint" in result.errors[0]


def test_crop_on_a_mode_with_a_mask_input_warns(tmp_path):
    result, _ = check(tmp_path, CROP)

    assert result.errors == []
    assert any("region: mask usually gives a better seam" in warning for warning in result.warnings)


def test_crop_without_a_mask_input_is_quiet(tmp_path):
    result, _ = check(tmp_path, CROP, fields=[PLAIN_IMAGE, *BASE_FIELDS[1:]])

    assert result.warnings == []


def test_a_prompt_task_on_a_promptless_mode_warns(tmp_path):
    result, _ = check(tmp_path, CROP, promptless=["edit"])

    assert any("takes none" in warning for warning in result.warnings)


def test_a_plugin_task_from_another_plugins_mode_is_an_error(tmp_path):
    result, _ = check(tmp_path, declaration(task(task="shiny.colorize")), owner="other")

    assert result.tasks == []
    assert "cannot be offered by a mode of plugin 'other'" in result.errors[0]


def test_a_plugin_may_offer_core_tasks_from_its_own_modes(tmp_path):
    result, _ = check(tmp_path, CROP, owner="shiny")

    assert [item.task for item in result.tasks] == ["edit_region"]


def test_owned_fields_cover_the_declaration(tmp_path):
    result, template = check(
        tmp_path,
        declaration(
            task(
                region="mask",
                inputs={"image": "source_image", "mask": "source_image_inpaint_mask"},
                size={"field": "resolution"},
                set={"output_size": "match"},
                owned=["match_source"],
            )
        ),
    )

    assert owned_fields(result.tasks[0], template.modes["edit"]) == {
        "source_image",
        "source_image_inpaint_mask",
        "resolution",
        "output_size",
        "match_source",
        "quantity",
    }


def test_the_cache_follows_the_file(tmp_path):
    root = tmp_path / "presets"
    root.mkdir()
    preset_dir = write_preset(root, CROP)
    loader = load(root)
    cache = DeclarationCache(lambda: loader.all(), dict)

    assert cache()[(PRESET_ID, "edit")].declaration is not None
    path = preset_dir / "modes" / "edit" / DECLARATION_FILE
    path.write_text("schema_version: 9\n# a longer body than before\n", encoding="utf-8")

    assert cache()[(PRESET_ID, "edit")].error is not None


def test_a_contributed_mode_reads_its_file_from_the_contributing_plugin(tmp_path):
    from types import SimpleNamespace

    from src.features.editor_tasks.declarations import declaration_path, mode_owner

    template = SimpleNamespace(id="P1", path=str(tmp_path / "core"), origin={"kind": "plugin", "plugin_id": "host"})
    core_mode = SimpleNamespace(source_plugin=None)
    contributed = SimpleNamespace(source_plugin="shiny")
    roots = {("shiny", "P1"): tmp_path / "plugin"}

    assert declaration_path(template, "edit", core_mode, roots) == tmp_path / "core" / "modes" / "edit" / "editor.yml"
    assert declaration_path(template, "edit", contributed, roots) == tmp_path / "plugin" / "modes" / "edit" / "editor.yml"
    assert declaration_path(template, "edit", contributed, {}) is None
    assert mode_owner(template, core_mode) == "host"
    assert mode_owner(template, contributed) == "shiny"
