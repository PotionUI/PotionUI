from types import SimpleNamespace

import pytest
import yaml

from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskDefinition, TaskRegistry, register_core_tasks
from src.features.presets.editor_mode_lint import lint_editor_mode
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


def editors(task_id):
    return ("image",)


class Checked:
    def __init__(self, loader, template, owner, promptless):
        self.loader = loader
        self.template = template
        self.load_errors = [message for messages in loader.load_errors.values() for message in messages]
        self.modes = template.editor_modes if template is not None else {}
        if promptless and template is not None:
            template.vars = {**(template.vars or {}), "promptless_modes": list(promptless)}
        self.issues = [
            issue
            for name, mode in self.modes.items()
            for issue in lint_editor_mode(template, name, mode, need, editors, owner)
        ]
        manager = EditorTaskManager(REGISTRY, lambda: loader.all(), lambda user: {PRESET_ID}, lambda engine: True)
        self.offered = [entry.spec.task for entry in manager.editor_entries()]

    @property
    def errors(self):
        return self.load_errors + [message for level, message in self.issues if level == "error"]

    @property
    def warnings(self):
        return [message for level, message in self.issues if level == "warning"]


def check(tmp_path, editor, fields=None, owner=None, promptless=(), mode="edit"):
    root = tmp_path / "presets"
    root.mkdir(exist_ok=True)
    write_preset(root, editor, fields=fields, mode=mode)
    loader = load(root)
    return Checked(loader, loader.get(PRESET_ID), owner, promptless)


def test_a_valid_crop_declaration_is_accepted(tmp_path):
    result = check(tmp_path, CROP)

    assert result.errors == []
    assert result.offered == ["edit_region"]
    spec = result.modes["edit"].editor
    assert spec.size["area"] == 1048576
    assert spec.size["multiple"] == 16
    assert spec.context == 0.5


def test_a_valid_mask_declaration_is_accepted(tmp_path):
    result = check(tmp_path, MASK)

    assert result.errors == []
    assert result.modes["edit"].editor.inputs["mask"] == "source_image_inpaint_mask"


def test_a_media_list_input_with_an_index_is_accepted(tmp_path):
    result = check(tmp_path, task(inputs={"source": {"field": "references", "index": 0}}))

    assert result.errors == []
    assert result.modes["edit"].editor.inputs["source"] == {"field": "references", "index": 0}


def test_no_file_means_no_offers(tmp_path):
    result = check(tmp_path, None)

    assert result.modes == {}
    assert result.offered == []
    assert result.errors == []


def test_an_empty_file_is_a_schema_error(tmp_path):
    result = check(tmp_path, "")

    assert result.offered == []
    assert any("must be a mapping" in message or "schema_version" in message for message in result.errors)


@pytest.mark.parametrize("version", [2, 0, "1"])
def test_an_unknown_or_wrong_schema_version_is_an_error(tmp_path, version):
    result = check(tmp_path, {**CROP, "schema_version": version})

    assert result.offered == []
    assert any("schema_version" in message for message in result.errors)


def test_a_missing_schema_version_is_an_error(tmp_path):
    result = check(tmp_path, {key: value for key, value in CROP.items() if key != "schema_version"})

    assert result.offered == []
    assert any("schema_version" in message for message in result.errors)


def test_an_unknown_key_is_an_error(tmp_path):
    result = check(tmp_path, task(colour="red"))

    assert result.offered == []
    assert any("colour" in message for message in result.errors)


def test_two_editor_modes_for_one_task_are_both_offered(tmp_path):
    result = check(tmp_path, declaration(task(), task(label="Other")))

    assert result.errors == []
    assert result.offered == ["edit_region", "edit_region"]


def test_an_unknown_core_task_is_an_error_and_is_not_offered(tmp_path):
    result = check(tmp_path, task(task="paint_it_red"))

    assert result.offered == []
    assert "unknown task 'paint_it_red'" in " ".join(result.errors)


def test_a_plugin_task_that_is_not_registered_is_a_warning_and_is_skipped(tmp_path):
    result = check(tmp_path, declaration(task(task="shiny.colorize"), task()))

    assert result.errors == []
    assert result.offered == ["edit_region"]
    assert "shiny.colorize" in result.warnings[0]


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"inputs": {"source": "nope"}}, "not a field of the mode"),
        ({"inputs": {"source": "seed"}}, "image field"),
        ({"inputs": {"source": {"field": "source_image", "index": 0}}}, "needs a media field"),
        ({"region": "mask", "inputs": {"source": "source_image"}}, "needs inputs.mask"),
        ({"region": "mask", "inputs": {"source": "source_image", "mask": "other_mask"}}, "must be 'source_image_inpaint_mask'"),
        ({"size": {"field": "seed"}}, "not a resolution field"),
        ({"size": {"field": "nope"}}, "not a field of the mode"),
        ({"size": {"multiple": 24}}, "multiple"),
        ({"size": {"area": 1000}}, "area"),
        ({"size": {"area": 9000000}}, "area"),
        ({"fixed": {"nope": 1}}, "not a field"),
        ({"fixed": {"output_size": "huge"}}, "not an option"),
        ({"fixed": {"match_source": "yes"}}, "true or false"),
        ({"inputs": {"source": "source_image", "seed": "nope"}}, "inputs.seed"),
        ({"inputs": {"source": "source_image", "quantity": "nope"}}, "inputs.quantity"),
        ({"fields": {"hide": ["nope"]}}, "not a field"),
    ],
)
def test_each_invalid_case_drops_the_mode(tmp_path, changes, message):
    result = check(tmp_path, task(**changes))

    assert result.offered == []
    assert any(message in error for error in result.errors), result.errors


def test_a_mask_input_needs_allow_inpaint_on_the_image_field(tmp_path):
    result = check(tmp_path, MASK, fields=[PLAIN_IMAGE, *BASE_FIELDS[1:]])

    assert result.offered == []
    assert any("allow_inpaint" in message for message in result.errors)


def test_crop_on_a_mode_with_a_mask_input_no_longer_warns(tmp_path):
    result = check(tmp_path, CROP)

    assert result.errors == []
    assert not any("region: mask usually gives a better seam" in warning for warning in result.warnings)


def test_crop_without_a_mask_input_is_quiet(tmp_path):
    result = check(tmp_path, CROP, fields=[PLAIN_IMAGE, *BASE_FIELDS[1:]])

    assert result.warnings == []


def test_a_prompt_task_on_a_promptless_mode_warns(tmp_path):
    result = check(tmp_path, CROP, promptless=["edit"])

    assert any("takes none" in warning for warning in result.warnings)
    assert result.offered == ["edit_region"]


def test_a_plugin_task_from_another_plugins_mode_is_an_error(tmp_path):
    result = check(tmp_path, task(task="shiny.colorize"), owner="other")

    assert any("cannot be offered by an editor mode of plugin 'other'" in message for message in result.errors)


def test_a_plugin_may_offer_core_tasks_from_its_own_modes(tmp_path):
    result = check(tmp_path, CROP, owner="shiny")

    assert result.errors == []


def test_the_owned_fields_cover_the_inputs_the_fixed_values_and_the_hidden_fields(tmp_path):
    result = check(
        tmp_path,
        task(
            region="mask",
            inputs={"source": "source_image", "mask": "source_image_inpaint_mask"},
            size={"field": "resolution"},
            fixed={"output_size": "match", "match_source": True},
            fields={"hide": ["resolution"]},
        ),
    )
    manager = EditorTaskManager(REGISTRY, lambda: result.loader.all(), lambda user: {PRESET_ID}, lambda engine: True)

    assert result.errors == []
    assert manager.owned_fields(PRESET_ID, "editor:edit") == {
        "source_image",
        "source_image_inpaint_mask",
        "resolution",
        "output_size",
        "match_source",
        "quantity",
    }


def test_the_manager_follows_a_reloaded_preset(tmp_path):
    root = tmp_path / "presets"
    root.mkdir()
    preset_dir = write_preset(root, CROP)
    loader = load(root)
    manager = EditorTaskManager(REGISTRY, lambda: loader.all(), lambda user: {PRESET_ID}, lambda engine: True)

    assert [entry.key for entry in manager.editor_entries()] == ["editor:edit"]
    (preset_dir / "editor_modes" / "edit" / "editor_mode.yml").write_text("schema_version: 9\n", encoding="utf-8")
    loader.reload()

    assert manager.editor_entries() == []


def test_a_contributed_editor_mode_is_offered_and_owned_by_the_contributing_plugin(tmp_path):
    root = tmp_path / "presets"
    root.mkdir()
    write_preset(root, None)
    plugin_dir = tmp_path / "plugin"
    target = plugin_dir / "contributed" / "editor_modes" / "relight"
    target.mkdir(parents=True)
    (target / "editor_mode.yml").write_text(yaml.safe_dump(task(task="shiny.relight")), encoding="utf-8")
    manifest = SimpleNamespace(
        id="shiny",
        plugin_dir=plugin_dir,
        preset_modes=[{"target": PRESET_ID, "modes_root": "contributed"}],
        preset_extensions=[],
    )
    from src.features.presets.loader import PresetTemplateLoader

    loader = PresetTemplateLoader([str(root)], plugin_registry=SimpleNamespace(get_enabled_plugins=lambda: [manifest]))
    loader.load_presets()
    registry = register_core_tasks(TaskRegistry())
    registry.register(TaskDefinition(id="shiny.relight", label="Relight", source="shiny"))
    manager = EditorTaskManager(registry, lambda: loader.all(), lambda user: {PRESET_ID}, lambda engine: True)

    entries = manager.editor_entries()

    assert [(entry.key, entry.mode.source_plugin) for entry in entries] == [("editor:relight", "shiny")]
    assert manager.owned_fields(PRESET_ID, "editor:relight") >= {"source_image"}
