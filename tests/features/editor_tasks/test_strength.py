from types import SimpleNamespace

import pytest

from src.features.editor_tasks.dto import StartEditorRunRequest
from src.features.editor_tasks.manager import EditorTaskManager
from src.features.editor_tasks.registry import TaskRegistry, register_core_tasks
from src.features.presets.editor_modes import clamp_strength
from src.features.presets.linter import PresetLinter
from tests.features.editor_tasks.support import BASE_FIELDS, CROP, load, task, write_preset

DENOISE = {"name": "denoise", "type": "slider", "label": "Denoise", "default": 0.4, "configuration": {"min": 0.1, "max": 1.0, "step": 0.05}}
FIELDS = [*BASE_FIELDS, DENOISE]
STRENGTH = {"field": "denoise", "default": 0.35, "min": 0.1, "max": 1.0}
USER = SimpleNamespace(id="u1")


def with_strength(**changes):
    return task(strength={**STRENGTH, **changes})


def errors(root):
    return [issue.message for issue in PresetLinter([str(root)]).lint() if issue.level == "error" and "editor_mode" in issue.message]


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "presets"
    folder.mkdir()
    return folder


def offers(root, editor):
    write_preset(root, editor, fields=FIELDS, preset_id="A-CROP")
    loader = load(root)
    manager = EditorTaskManager(register_core_tasks(TaskRegistry()), lambda: loader.all(), lambda user: {"A-CROP"}, lambda engine: True)
    return next(item for item in manager.offers(USER) if item["id"] == "edit_region")["offers"]


def test_a_good_strength_is_quiet_and_reaches_the_spec(root):
    write_preset(root, with_strength(), fields=FIELDS)

    assert errors(root) == []
    loader = load(root)
    assert loader.all()[0].editor_modes["edit"].editor.strength == STRENGTH


def test_a_mode_without_strength_has_none(root):
    write_preset(root, CROP, fields=FIELDS)

    assert load(root).all()[0].editor_modes["edit"].editor.strength is None


@pytest.mark.parametrize(
    "editor, message",
    [
        (with_strength(field="nope"), "not a field"),
        (with_strength(field="resolution"), "not a number field"),
        (with_strength(default=1.5), "default"),
        (with_strength(default=0.05), "default"),
        (with_strength(min=0.5, max=0.5, default=0.5), "min"),
        (with_strength(min=0.0), "outside the range"),
        (with_strength(max=2.0), "outside the range"),
        (task(strength={"field": "denoise", "default": 0.3, "min": 0.1}), "max"),
        (task(strength={**STRENGTH, "extra": 1}), "extra"),
        (task(strength={**STRENGTH}, fixed={"denoise": 0.4}), "more than one"),
        (task(strength={"field": "source_image", "default": 0.3, "min": 0.1, "max": 1.0}), "more than one"),
    ],
)
def test_each_strength_rule_reports_an_error(root, editor, message):
    write_preset(root, editor, fields=FIELDS)

    found = errors(root)

    assert found and any(message in text for text in found), found


def test_hiding_the_strength_field_is_allowed(root):
    write_preset(root, task(strength=STRENGTH, fields={"hide": ["denoise"]}), fields=FIELDS)

    assert errors(root) == []


def test_the_offer_carries_the_strength_range_and_default(root):
    found = offers(root, with_strength())

    assert found[0]["strength"] == {"default": 0.35, "min": 0.1, "max": 1.0}


def test_the_offer_says_null_when_the_mode_has_no_strength(root):
    assert offers(root, CROP)[0]["strength"] is None


def test_the_run_request_takes_an_optional_strength():
    base = {
        "edit": {"id": "ed_1"},
        "input": {"item_id": "picture"},
        "setup": {"preset_id": "A", "mode": "editor:edit"},
    }

    assert StartEditorRunRequest.model_validate(base).strength is None
    assert StartEditorRunRequest.model_validate({**base, "strength": 0.6}).strength == 0.6
    with pytest.raises(ValueError):
        StartEditorRunRequest.model_validate({**base, "strength": -0.1})


def test_a_requested_strength_is_clamped_into_the_range_and_defaults_when_absent(root):
    write_preset(root, with_strength(min=0.2, max=0.8), fields=FIELDS)
    spec = load(root).all()[0].editor_modes["edit"].editor

    assert clamp_strength(spec, None) == ("denoise", 0.35)
    assert clamp_strength(spec, 0.5) == ("denoise", 0.5)
    assert clamp_strength(spec, 3.0) == ("denoise", 0.8)
    assert clamp_strength(spec, 0.0) == ("denoise", 0.2)


def test_a_mode_without_strength_ignores_a_requested_one(root):
    write_preset(root, CROP, fields=FIELDS)

    assert clamp_strength(load(root).all()[0].editor_modes["edit"].editor, 0.5) is None
