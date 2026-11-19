from pathlib import Path

import pytest

from src.features.presets.linter import PresetLinter
from tests.features.editor_tasks.support import (
    BASE_FIELDS,
    CROP,
    MASK,
    PLAIN_IMAGE,
    declaration,
    task,
    write_preset,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO_ROOT / "content" / "presets" / "marketplace"


def editor_issues(root, level=None):
    return [
        issue
        for issue in PresetLinter([str(root)]).lint()
        if ("editor_mode" in issue.message or "editor.yml" in issue.message) and (level is None or issue.level == level)
    ]


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "presets"
    folder.mkdir()
    return folder


def test_a_good_file_is_quiet(root):
    write_preset(root, MASK)

    assert [issue for issue in editor_issues(root) if issue.level != "info"] == []


def test_no_file_is_quiet(root):
    write_preset(root, None)

    assert editor_issues(root) == []


@pytest.mark.parametrize(
    "editor, message",
    [
        ({**CROP, "schema_version": 2}, "schema_version"),
        ({key: value for key, value in CROP.items() if key != "schema_version"}, "schema_version"),
        (task(colour="red"), "colour"),
        (task(task="nonsense"), "unknown task"),
        (task(inputs={"source": "nope"}), "not a field of the mode"),
        (task(inputs={"source": "seed"}), "image field"),
        (task(region="mask"), "needs inputs.mask"),
        (task(region="mask", inputs={"source": "source_image", "mask": "x"}), "inputs.mask must be"),
        (task(size={"field": "seed"}), "not a resolution field"),
        (task(size={"multiple": 5}), "multiple"),
        (task(size={"area": 10}), "area"),
        (task(fixed={"nope": 1}), "not a field"),
        (task(fixed={"output_size": "huge"}), "not an option"),
        (task(inputs={"source": "source_image", "seed": "nope"}), "inputs.seed"),
        (task(inputs={"source": "source_image", "quantity": "nope"}), "inputs.quantity"),
        (task(fields={"hide": ["nope"]}), "not a field"),
        (task(**{"from": "missing"}), "missing"),
        ("schema_version: [", "editor_mode.yml"),
    ],
)
def test_each_rule_reports_an_error(root, editor, message):
    write_preset(root, editor)

    errors = editor_issues(root, "error")

    assert errors and any(message in issue.message for issue in errors), [i.message for i in errors]


def test_a_mask_without_allow_inpaint_reports_an_error(root):
    write_preset(root, MASK, fields=[PLAIN_IMAGE, *BASE_FIELDS[1:]])

    assert any("allow_inpaint" in issue.message for issue in editor_issues(root, "error"))


def test_an_unregistered_plugin_task_is_a_warning(root):
    write_preset(root, task(task="shiny.colorize"))

    assert editor_issues(root, "error") == []
    assert any("not installed or enabled" in issue.message for issue in editor_issues(root, "warning"))


def test_crop_on_a_mask_mode_is_quiet(root):
    write_preset(root, CROP)

    assert editor_issues(root, "error") == []
    assert editor_issues(root, "warning") == []


def test_a_prompt_task_on_a_promptless_mode_is_a_warning(root):
    write_preset(root, CROP, extra_manifest={"vars": {"promptless_modes": ["edit"]}})

    assert any("takes none" in issue.message for issue in editor_issues(root, "warning"))


def test_a_leftover_editor_yml_beside_its_editor_mode_is_a_warning(root):
    preset_dir = write_preset(root, CROP)
    (preset_dir / "modes" / "edit" / "editor.yml").write_text("schema_version: 1\ntasks: []\n", encoding="utf-8")

    warnings = editor_issues(root, "warning")

    assert any("modes/edit/editor.yml: no longer read" in issue.message for issue in warnings)


def test_an_editor_yml_alone_is_not_reported(root):
    preset_dir = write_preset(root, None)
    (preset_dir / "modes" / "edit" / "editor.yml").write_text("schema_version: 1\ntasks: []\n", encoding="utf-8")

    assert editor_issues(root) == []


def test_the_shipped_editor_modes_have_no_errors_and_no_warnings():
    files = sorted(MARKETPLACE.glob("*/editor_modes/*/editor_mode.yml"))

    assert len(files) == 25
    issues = [issue for issue in editor_issues(MARKETPLACE) if issue.level != "info"]
    assert [issue.message for issue in issues] == []


def test_no_shipped_preset_keeps_an_editor_yml():
    assert sorted(MARKETPLACE.glob("*/modes/*/editor.yml")) == []
